import argparse
import gc
import os
from pathlib import Path
import warnings

import asdf
import numpy as np
import yaml
#from np.fft import fftfreq, fftn, ifftn
from scipy.fft import fftfreq, rfftfreq, rfftn, irfftn

from abacusnbody.metadata import get_meta

from asdf.exceptions import AsdfWarning
warnings.filterwarnings('ignore', category=AsdfWarning)

DEFAULTS = {'path2config': 'config/abacus_hod.yaml', 'cv_type': 'zcv'}

def compress_asdf(asdf_fn, table, header):
    r"""
    Compress the dictionaries `table` and `header` using blsc into an ASDF file, `asdf_fn`.
    """
    # cram into a dictionary
    data_dict = {}
    for field in table.keys():
        data_dict[field] = table[field]

    # create data tree structure
    data_tree = {
        "data": data_dict,
        "header": header,
    }

    # set compression options here
    compression_kwargs=dict(typesize="auto", shuffle="shuffle", compression_block_size=12*1024**2, blosc_block_size=3*1024**2, nthreads=4)
    with asdf.AsdfFile(data_tree) as af, open(asdf_fn, 'wb') as fp: # where data_tree is the ASDF dict tree structure
        af.write_to(fp, all_array_compression='blsc', compression_kwargs=compression_kwargs)

def load_dens(ic_dir, sim_name, nmesh):
    """
    Load initial condition density field for the given AbacusSummit simulation.
    """
    f = asdf.open(Path(ic_dir) / sim_name / f"ic_dens_N{nmesh:d}.asdf")
    delta_lin = f['data']['density'][:, :, :]
    f.close()
    return delta_lin

def load_disp(ic_dir, sim_name, nmesh):
    """
    Load initial condition displacement fields for the given AbacusSummit simulation.
    """
    f = asdf.open(Path(ic_dir) / sim_name / f"ic_disp_N{nmesh:d}.asdf")
    Lbox = f['header']['BoxSize']
    psi_x = f['data']['displacements'][:, :, :, 0]/Lbox
    psi_y = f['data']['displacements'][:, :, :, 1]/Lbox
    psi_z = f['data']['displacements'][:, :, :, 2]/Lbox
    f.close()
    return psi_x, psi_y, psi_z


def gaussian_filter(field, nmesh, lbox, kcut):
    """
    Apply a fourier space gaussian filter to a field.

    Parameters
    ---------
    field : array_like
        the field to filter.
    nmesh : int
        size of the mesh.
    lbox : float
        size of the box.
    kcut : float
        the exponential cutoff to use in the gaussian filter

    Returns
    -------
    f_filt : array_like
        Gaussian filtered version of field
    """

    # fourier transform field
    fhat = rfftn(field, workers=-1).astype(np.complex64)
    kvals = fftfreq(nmesh) * (2 * np.pi * nmesh) / lbox
    kvalsr = rfftfreq(nmesh) * (2 * np.pi * nmesh) / lbox
    kvals = kvals.astype(np.float32)
    kvalsr = kvalsr.astype(np.float32)

    # multiply by filter in fourier space
    kx, ky, kz = np.meshgrid(kvals, kvals, kvalsr)
    knorm = kx**2 + ky**2 + kz**2
    del kx, ky, kz
    gc.collect()
    filt = np.exp(-knorm / (2 * kcut**2))
    del knorm
    gc.collect()
    fhat = filt * fhat
    del filt
    gc.collect()

    # inverse fourier transform
    f_filt = irfftn(fhat, workers=-1).astype(np.float32)
    return f_filt

def delta_ij(i, j):
    """
    Output Kronecker delta function.
    """
    if i == j:
        return 1.
    else:
        return 0.

def get_dk_to_s2(delta_k, nmesh, lbox):
    r"""
    Computes the square tidal field from the density FFT `s^2 = s_ij s_ij`,
    where `s_ij = (k_i k_j / k^2 - delta_ij / 3 ) * delta_k`.

    Parameters
    ----------
    delta_k : array_like
        Fourier transformed density field.
    nmesh : int
        size of the mesh.
    lbox : float
        size of the box.

    Returns
    -------
    tidesq :
        the tidal field (s^2).
    """
    # get wavenumber grids # TODO could carry this around instead of regenerating every time
    kvals = fftfreq(nmesh) * (2 * np.pi * nmesh) / lbox
    kvalsr = rfftfreq(nmesh) * (2 * np.pi * nmesh) / lbox
    kvals = kvals.astype(np.float32)
    kvalsr = kvalsr.astype(np.float32)
    kx, ky, kz = np.meshgrid(kvals, kvals, kvalsr)
    knorm = kx**2 + ky**2 + kz**2
    knorm[0, 0, 0] = 1.
    gc.collect()

    # Compute the symmetric tide at every Fourier mode which we'll reshape later
    # Order is xx, xy, xz, yy, yz, zz
    jvec = [[0, 0], [0, 1], [0, 2], [1, 1], [1, 2], [2, 2]]
    ks = [kx, ky, kz]
    del kx, ky, kz
    gc.collect()

    # compute s_ij and do the summation
    tidesq = np.zeros((nmesh, nmesh, nmesh), dtype=np.float32)
    for i in range(len(jvec)):
        karray = ks[jvec[i][0]]*ks[jvec[i][1]]/knorm - delta_ij(jvec[i][0], jvec[i][1]) / 3.0
        if jvec[i][0] != jvec[i][1]:
            tidesq += 2.0 * irfftn(karray * delta_k, workers=-1)**2.
        else:
            tidesq += 1.0 * irfftn(karray * delta_k, workers=-1)**2.
        del karray
        gc.collect()
    return tidesq


def get_dk_to_n2(delta_k, nmesh, lbox):
    """
    Computes the density curvature from the density field: nabla^2 delta = IFFT(-k^2 delta_k)
    Parameters
    ----------
    delta_k : array_like
        Fourier transformed density field.
    nmesh : int
        size of the mesh.
    lbox : float
        size of the box.

    Returns
    -------
    real_gradsqdelta : array_like
        the nabla^2 delta field
    """
    # get wavenumber grids
    kvals = fftfreq(nmesh) * (2 * np.pi * nmesh) / lbox
    kvalsr = rfftfreq(nmesh) * (2 * np.pi * nmesh) / lbox
    kvals = kvals.astype(np.float32)
    kvalsr = kvalsr.astype(np.float32)
    kx, ky, kz = np.meshgrid(kvals, kvals, kvalsr)
    knorm = kx**2 + ky**2 + kz**2
    knorm[0, 0, 0] = 1.
    del kx, ky, kz
    gc.collect()

    # Compute -k^2 delta which is the gradient
    nabla2delta = irfftn(-knorm * delta_k, workers=-1).astype(np.float32)
    return nabla2delta

def get_fields(delta_lin, Lbox, nmesh):
    """
    Return the fields delta, delta^2, s^2, nabla^2 given the linear density field.
    """
    # get delta
    delta_fft = rfftn(delta_lin, workers=-1).astype(np.complex64)
    fmean = np.mean(delta_lin)
    d = delta_lin-fmean
    print("Generated delta")

    # get delta^2
    d2 = delta_lin * delta_lin
    fmean = np.mean(d2)
    d2 -= fmean
    del delta_lin
    gc.collect()
    print("Generated delta^2")

    # get s^2
    s2 = get_dk_to_s2(delta_fft, nmesh, Lbox)
    fmean = np.mean(s2)
    s2 -= fmean
    print("Generated s_ij s^ij")

    # get n^2
    n2 = get_dk_to_n2(delta_fft, nmesh, Lbox)
    print("Generated nabla^2")
    return d, d2, s2, n2

def main(path2config, cv_type, alt_simname=None, verbose=False):
    r"""
    Save the initial conditions fields (1cb, delta, delta^2, s^2, nabla^2) as ASDF files.

    Note: you can save the fields separately if they are using too much memory.
    TODO: the multiplications in Fourier space can be sped up with numba.

    Parameters
    ----------
    path2config : str
        name of the yaml containing parameter specifications.
    cv_type : str
        specifies where to find CV parameters in the yaml file ('zcv' or 'lcv')
    alt_simname : str, optional
        specify simulation name if different from yaml file.
    verbose : bool, optional
        print some useful benchmark statements. Default is False.
    """

    # read zcv parameters
    config = yaml.safe_load(open(path2config))
    if cv_type == "zcv":
        zcv_dir = config['zcv_params']['zcv_dir']
        ic_dir = config['zcv_params']['ic_dir']
        nmesh = config['zcv_params']['nmesh']
        kcut = config['zcv_params']['kcut']
    elif cv_type == "lcv":
        zcv_dir = config['lcv_params']['lcv_dir']
        ic_dir = config['lcv_params']['ic_dir']
        nmesh = config['lcv_params']['nmesh']
        kcut = config['lcv_params']['kcut']
    if alt_simname is not None:
        sim_name = alt_simname
    else:
        sim_name = config['sim_params']['sim_name']
    z_this = config['sim_params']['z_mock'] # doesn't matter
    if verbose:
        print("Read CV parameters")

    # create save directory
    save_dir = Path(zcv_dir) / sim_name
    os.makedirs(save_dir, exist_ok=True)

    # get a few parameters for the simulation
    meta = get_meta(sim_name, redshift=z_this)
    Lbox = meta['BoxSize']

    # file to save the filtered ic
    ic_fn = Path(save_dir) / f"ic_filt_nmesh{nmesh:d}.asdf"
    fields_fn = Path(save_dir) / f"fields_nmesh{nmesh:d}.asdf"

    # check if filtered ic saved
    if os.path.exists(ic_fn):
        # load density and displacement fields
        f = asdf.open(ic_fn)
        dens = f['data']['dens'][:, :, :]
        disp_x = f['data']['disp_x'][:, :, :]
        disp_y = f['data']['disp_y'][:, :, :]
        disp_z = f['data']['disp_z'][:, :, :]
        f.close()
    else:
        # load density field
        dens = load_dens(ic_dir, sim_name, nmesh)
        if verbose:
            print("Loaded density field")

        # load displacement field
        disp_x, disp_y, disp_z = load_disp(ic_dir, sim_name, nmesh)
        if verbose:
            print("Loaded displacement field")

        # apply filtering at 0.5 k_Ny
        dens = gaussian_filter(dens, nmesh, Lbox, kcut)
        disp_x = gaussian_filter(disp_x, nmesh, Lbox, kcut)
        disp_y = gaussian_filter(disp_y, nmesh, Lbox, kcut)
        disp_z = gaussian_filter(disp_z, nmesh, Lbox, kcut)
        if verbose:
            print("Applied Gaussian filter")

        # save filtered field using asdf compression
        header = {}
        header['sim_name'] = sim_name
        header['Lbox'] = Lbox
        header['nmesh'] = nmesh
        header['kcut'] = kcut
        table = {}
        table['dens'] = dens
        table['disp_x'] = disp_x
        table['disp_y'] = disp_y
        table['disp_z'] = disp_z
        compress_asdf(str(ic_fn), table, header)
        if verbose:
            print("Saved filtered displacement and density fields")

    # not sure what the displacements are used for (maybe later?)
    del disp_x, disp_y, disp_z
    gc.collect()

    if os.path.exists(fields_fn):
        print("Already saved fields for this simulation")
    else:
        # compute the fields
        d, d2, s2, n2 = get_fields(dens, Lbox, nmesh)
        #print("fields dtype (float32)", d.dtype, d2.dtype, s2.dtype, n2.dtype)

        # save fields using asdf compression
        header = {}
        header['sim_name'] = sim_name
        header['Lbox'] = Lbox
        header['nmesh'] = nmesh
        header['kcut'] = kcut
        table = {}
        table['delta'] = d
        table['delta2'] = d2
        table['nabla2'] = n2
        table['tidal2'] = s2
        compress_asdf(str(fields_fn), table, header)
        print("Saved all filtered fields for this simulation")

class ArgParseFormatter(argparse.RawDescriptionHelpFormatter, argparse.ArgumentDefaultsHelpFormatter):
    pass

if __name__ == "__main__":

    # parsing arguments
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=ArgParseFormatter)
    parser.add_argument('--path2config', help='Path to the config file', default=DEFAULTS['path2config'])
    parser.add_argument('--alt_simname', help='Alternative simulation name')
    parser.add_argument('--cv_type', help='Type of control variates to use (ZelDovich or Linear)', default=DEFAULTS['cv_type'])
    parser.add_argument('--verbose', action='store_true', help='Print out useful statements')
    args = vars(parser.parse_args())
    main(**args)
