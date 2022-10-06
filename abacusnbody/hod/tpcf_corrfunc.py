# allsims testhod rsd
import numpy as np
import os, sys, time, gc
import Corrfunc
# from Corrfunc.mocks.DDrppi_mocks import DDrppi_mocks
# from Corrfunc.utils import convert_3d_counts_to_cf, convert_rp_pi_counts_to_wp
# from Corrfunc.theory.DDrppi import 
from Corrfunc.theory import wp, xi, DDsmu, DDrppi
from scipy.special import legendre
from numba import njit
import numba

def tpcf_multipole(s_mu_tcpf_result, mu_bins, order=0):
    r"""
    Calculate the multipoles of the two point correlation function
    after first computing `~halotools.mock_observables.s_mu_tpcf`.
    This is copied over from halotools. Original author was Duncan Campbell.
    Parameters
    ----------
    s_mu_tcpf_result : np.ndarray
        2-D array with the two point correlation function calculated in bins
        of :math:`s` and :math:`\mu`.  See `~halotools.mock_observables.s_mu_tpcf`.
    mu_bins : array_like
        array of :math:`\mu = \cos(\theta_{\rm LOS})`
        bins for which ``s_mu_tcpf_result`` has been calculated.
        Must be between [0,1].
    order : int, optional
        order of the multpole returned.
    Returns
    -------
    xi_l : np.array
        multipole of ``s_mu_tcpf_result`` of the indicated order.
    Examples
    --------
    For demonstration purposes we create a randomly distributed set of points within a
    periodic cube of length 250 Mpc/h.
    >>> Npts = 100
    >>> Lbox = 250.
    >>> x = np.random.uniform(0, Lbox, Npts)
    >>> y = np.random.uniform(0, Lbox, Npts)
    >>> z = np.random.uniform(0, Lbox, Npts)
    We transform our *x, y, z* points into the array shape used by the pair-counter by
    taking the transpose of the result of `numpy.vstack`. This boilerplate transformation
    is used throughout the `~halotools.mock_observables` sub-package:
    >>> sample1 = np.vstack((x,y,z)).T
    First, we calculate the correlation function using
    `~halotools.mock_observables.s_mu_tpcf`.
    >>> from halotools.mock_observables import s_mu_tpcf
    >>> s_bins  = np.linspace(0.01, 25, 10)
    >>> mu_bins = np.linspace(0, 1, 15)
    >>> xi_s_mu = s_mu_tpcf(sample1, s_bins, mu_bins, period=Lbox)
    Then, we can calculate the quadrapole of the correlation function:
    >>> xi_2 = tpcf_multipole(xi_s_mu, mu_bins, order=2)
    """

    # process inputs
    s_mu_tcpf_result = np.atleast_1d(s_mu_tcpf_result)
    mu_bins = np.atleast_1d(mu_bins)
    order = int(order)

    # calculate the center of each mu bin
    mu_bin_centers = (mu_bins[:-1]+mu_bins[1:])/(2.0)

    # get the Legendre polynomial of the desired order.
    Ln = legendre(order)

    # numerically integrate over mu
    result = (2.0*order + 1.0)/2.0 * np.sum(s_mu_tcpf_result * np.diff(mu_bins) *\
        (Ln(mu_bin_centers) + Ln(-1.0*mu_bin_centers)), axis=1)

    return result

def calc_xirppi_fast(x1, y1, z1, rpbins, pimax, 
    pi_bin_size, lbox, Nthread, num_cells = 20, x2 = None, y2 = None, z2 = None):  # all r assumed to be in h-1 mpc units. 
    start = time.time()
    if not isinstance(pimax, int):
        raise ValueError("pimax needs to be an integer")
    if not isinstance(pi_bin_size, int):
        raise ValueError("pi_bin_size needs to be an integer")
    if not pimax % pi_bin_size == 0:
        raise ValueError("pi_bin_size needs to be an integer divisor of pimax, current values are ", pi_bin_size, pimax)

    ND1 = float(len(x1))
    if x2 is not None:
        ND2 = len(x2)
        autocorr = 0
    else:
        autocorr = 1
        ND2 = ND1
    
    # single precision mode
    # to do: make this native 
    cf_start = time.time()
    rpbins = rpbins.astype(np.float32)
    pimax = np.float32(pimax)
    x1 = x1.astype(np.float32)
    y1 = y1.astype(np.float32)
    z1 = z1.astype(np.float32)
    lbox = np.float32(lbox)

    if autocorr == 1:    
        results = DDrppi(autocorr, Nthread, pimax, rpbins, x1, y1, z1,
            boxsize = lbox, periodic = True, max_cells_per_dim = num_cells, verbose = False)
        DD_counts = results['npairs']
    else:
        x2 = x2.astype(np.float32)
        y2 = y2.astype(np.float32)
        z2 = z2.astype(np.float32)
        results = DDrppi(autocorr, Nthread, pimax, rpbins, x1, y1, z1, X2 = x2, Y2 = y2, Z2 = z2, 
            boxsize = lbox, periodic = True, max_cells_per_dim = num_cells, verbose = False)
        DD_counts = results['npairs']
    print("corrfunc took time ", time.time() - cf_start)

    DD_counts_new = np.array([np.sum(DD_counts[i:i+pi_bin_size]) for i in range(0, len(DD_counts), pi_bin_size)])
    DD_counts_new = DD_counts_new.reshape((len(rpbins) - 1, int(pimax/pi_bin_size)))

    # RR_counts_new = np.zeros((len(rpbins) - 1, int(pimax/pi_bin_size)))
    RR_counts_new = np.pi*(rpbins[1:]**2 - rpbins[:-1]**2)*pi_bin_size / lbox**3 * ND1 * ND2 * 2
    xirppi = DD_counts_new / RR_counts_new[:, None] - 1
    print("corrfunc took ", time.time() - start, "ngal ", len(x1))
    return xirppi


def calc_multipole_fast(x1, y1, z1, rpbins, 
    lbox, Nthread, num_cells = 20, x2 = None, y2 = None, z2 = None, orders = [0, 2]):  # all r assumed to be in h-1 mpc units. 

    ND1 = float(len(x1))
    if x2 is not None:
        ND2 = len(x2)
        autocorr = 0
    else:
        autocorr = 1
        ND2 = ND1
    
    # single precision mode
    # to do: make this native 
    cf_start = time.time()
    rpbins = rpbins.astype(np.float32)
    x1 = x1.astype(np.float32)
    y1 = y1.astype(np.float32)
    z1 = z1.astype(np.float32)
    pos1 = np.array([x1, y1, z1]).T % lbox
    lbox = np.float32(lbox)

    # mu_bins = np.linspace(0, 1, 20)

    # if autocorr == 1: 
    #     xi_s_mu = s_mu_tpcf(pos1, rpbins, mu_bins, period = lbox, num_threads = Nthread)
    #     print("halotools ", xi_s_mu)
    # else:
    #     xi_s_mu = s_mu_tpcf(pos1, rpbins, mu_bins, sample2 = np.array([x2, y2, z2]).T % lbox, period = lbox, num_threads = Nthread)

    nbins_mu = 40
    if autocorr == 1: 
        results = DDsmu(autocorr, Nthread, rpbins, 1, nbins_mu, x1, y1, z1, periodic = True, boxsize = lbox, max_cells_per_dim = num_cells)
        DD_counts = results['npairs']
    else:
        x2 = x2.astype(np.float32)
        y2 = y2.astype(np.float32)
        z2 = z2.astype(np.float32)
        results = DDsmu(autocorr, Nthread, rpbins, 1, nbins_mu, x1, y1, z1, X2 = x2, Y2 = y2, Z2 = z2, 
            periodic = True, boxsize = lbox, max_cells_per_dim = num_cells)
        DD_counts = results['npairs']
    DD_counts = DD_counts.reshape((len(rpbins) - 1, nbins_mu))

    mu_bins = np.linspace(0, 1, nbins_mu+1)
    RR_counts = 2*np.pi/3*(rpbins[1:, None]**3 - rpbins[:-1, None]**3)*(mu_bins[None, 1:] - mu_bins[None, :-1]) / lbox**3 * ND1 * ND2 * 2

    xi_s_mu = DD_counts / RR_counts - 1

    xi_array = []
    for neworder in orders:
        # print(neworder, rpbins, tpcf_multipole(xi_s_mu, mu_bins, order = neworder))
        xi_array += [tpcf_multipole(xi_s_mu, mu_bins, order=neworder)]
    xi_array = np.concatenate(xi_array)

    return xi_array


def calc_wp_fast(x1, y1, z1, rpbins, pimax, 
    lbox, Nthread, num_cells = 30, x2 = None, y2 = None, z2 = None):  # all r assumed to be in h-1 mpc units. 
    if not isinstance(pimax, int):
        raise ValueError("pimax needs to be an integer")

    ND1 = float(len(x1))
    if x2 is not None:
        ND2 = len(x2)
        autocorr = 0
    else:
        autocorr = 1
        ND2 = ND1

    # single precision mode
    # to do: make this native 
    cf_start = time.time()
    rpbins = rpbins.astype(np.float32)
    pimax = np.float32(pimax)
    x1 = x1.astype(np.float32)
    y1 = y1.astype(np.float32)
    z1 = z1.astype(np.float32)
    lbox = np.float32(lbox)

    if autocorr == 1:    
        print("sample size", len(x1))
        results = DDrppi(autocorr, Nthread, pimax, rpbins, x1, y1, z1,
            boxsize = lbox, periodic = True, max_cells_per_dim = num_cells)
        DD_counts = results['npairs']
    else:
        print("sample size", len(x1), len(x2))
        x2 = x2.astype(np.float32)
        y2 = y2.astype(np.float32)
        z2 = z2.astype(np.float32)
        results = DDrppi(autocorr, Nthread, pimax, rpbins, x1, y1, z1, X2 = x2, Y2 = y2, Z2 = z2, 
            boxsize = lbox, periodic = True, max_cells_per_dim = num_cells)
        DD_counts = results['npairs']
    print("corrfunc took time ", time.time() - cf_start)
    DD_counts = DD_counts.reshape((len(rpbins) - 1, int(pimax)))

    # RR_counts = np.zeros((len(rpbins) - 1, int(pimax)))
    # for i in range(len(rpbins) - 1):
    RR_counts = np.pi*(rpbins[1:]**2 - rpbins[:-1]**2) / lbox**3 * ND1 * ND2 * 2
    xirppi = DD_counts / RR_counts[:, None] - 1

    return 2*np.sum(xirppi, axis = 1)

def get_k_mu_box(L_hMpc, n_xy, n_z):
    """
    Compute the size of the k vector and mu for each mode. Assumes z direction is LOS
    """
    # cell width in (x,y) directions (Mpc/h)
    d_xy = L_hMpc / n_xy
    k_xy = np.fft.fftfreq(n_xy, d=d_xy) * 2. * np.pi

    # cell width in z direction (Mpc/h)
    d_z = L_hMpc / n_z
    k_z = np.fft.fftfreq(n_z, d=d_z) * 2. * np.pi

    # h/Mpc
    x = k_xy[:, np.newaxis, np.newaxis]
    y = k_xy[np.newaxis, :, np.newaxis]
    z = k_z[np.newaxis, np.newaxis, :]
    k_box = np.sqrt(x**2 + y**2 + z**2)

    # construct mu in two steps, without NaN warnings
    mu_box = z/np.ones_like(k_box)
    mu_box[k_box > 0.] /= k_box[k_box > 0.]
    mu_box[k_box == 0.] = 0.#np.nan
    return k_box, mu_box

@numba.vectorize
def rightwrap(x, L):
    if x >= L:
        return x - L
    return x


@njit(nogil=True)
def numba_cic_3D(positions, density, boxsize, weights=np.empty(0)):
    """
    Compute density using the cloud-in-cell algorithm. Assumes cubic box
    """
    gx = np.uint32(density.shape[0])
    gy = np.uint32(density.shape[1])
    gz = np.uint32(density.shape[2])
    threeD = gz != 1
    W = 1.
    Nw = len(weights)
    for n in range(len(positions)):
        # broadcast scalar weights
        if Nw == 1:
            W = weights[0]
        elif Nw > 1:
            W = weights[n]

        # convert to a position in the grid
        px = (positions[n,0]/boxsize)*gx # used to say boxsize+0.5
        py = (positions[n,1]/boxsize)*gy # used to say boxsize+0.5
        if threeD:
            pz = (positions[n,2]/boxsize)*gz # used to say boxsize+0.5

        # round to nearest cell center
        ix = np.int32(round(px))
        iy = np.int32(round(py))
        if threeD:
            iz = np.int32(round(pz))

        # calculate distance to cell center
        dx = ix - px
        dy = iy - py
        if threeD:
            dz = iz - pz

        # find the tsc weights for each dimension
        wx = 1. - np.abs(dx)
        if dx > 0.: # on the right of the center ( < )
            wxm1 = dx
            wxp1 = 0.
        else: # on the left of the center
            wxp1 = -dx
            wxm1 = 0.
        wy = 1. - np.abs(dy)
        if dy > 0.:
            wym1 = dy
            wyp1 = 0.
        else:
            wyp1 = -dy
            wym1 = 0.
        if threeD:
            wz = 1. - np.abs(dz)
            if dz > 0.:
                wzm1 = dz
                wzp1 = 0.
            else:
                wzp1 = -dz
                wzm1 = 0.
        else:
            wz = 1.

        # find the wrapped x,y,z grid locations of the points we need to change
        # negative indices will be automatically wrapped
        ixm1 = rightwrap(ix - 1, gx)
        ixw  = rightwrap(ix    , gx)
        ixp1 = rightwrap(ix + 1, gx)
        iym1 = rightwrap(iy - 1, gy)
        iyw  = rightwrap(iy    , gy)
        iyp1 = rightwrap(iy + 1, gy)
        if threeD:
            izm1 = rightwrap(iz - 1, gz)
            izw  = rightwrap(iz    , gz)
            izp1 = rightwrap(iz + 1, gz)
        else:
            izw = np.uint32(0)

        # change the 9 or 27 cells that the cloud touches
        density[ixm1, iym1, izw ] += wxm1*wym1*wz  *W
        density[ixm1, iyw , izw ] += wxm1*wy  *wz  *W
        density[ixm1, iyp1, izw ] += wxm1*wyp1*wz  *W
        density[ixw , iym1, izw ] += wx  *wym1*wz  *W
        density[ixw , iyw , izw ] += wx  *wy  *wz  *W
        density[ixw , iyp1, izw ] += wx  *wyp1*wz  *W
        density[ixp1, iym1, izw ] += wxp1*wym1*wz  *W
        density[ixp1, iyw , izw ] += wxp1*wy  *wz  *W
        density[ixp1, iyp1, izw ] += wxp1*wyp1*wz  *W

        if threeD:
            density[ixm1, iym1, izm1] += wxm1*wym1*wzm1*W
            density[ixm1, iym1, izp1] += wxm1*wym1*wzp1*W

            density[ixm1, iyw , izm1] += wxm1*wy  *wzm1*W
            density[ixm1, iyw , izp1] += wxm1*wy  *wzp1*W

            density[ixm1, iyp1, izm1] += wxm1*wyp1*wzm1*W
            density[ixm1, iyp1, izp1] += wxm1*wyp1*wzp1*W

            density[ixw , iym1, izm1] += wx  *wym1*wzm1*W
            density[ixw , iym1, izp1] += wx  *wym1*wzp1*W

            density[ixw , iyw , izm1] += wx  *wy  *wzm1*W
            density[ixw , iyw , izp1] += wx  *wy  *wzp1*W

            density[ixw , iyp1, izm1] += wx  *wyp1*wzm1*W
            density[ixw , iyp1, izp1] += wx  *wyp1*wzp1*W

            density[ixp1, iym1, izm1] += wxp1*wym1*wzm1*W
            density[ixp1, iym1, izp1] += wxp1*wym1*wzp1*W

            density[ixp1, iyw , izm1] += wxp1*wy  *wzm1*W
            density[ixp1, iyw , izp1] += wxp1*wy  *wzp1*W

            density[ixp1, iyp1, izm1] += wxp1*wyp1*wzm1*W
            density[ixp1, iyp1, izp1] += wxp1*wyp1*wzp1*W

@njit(nogil=True)
def numba_tsc_3D(positions, density, boxsize, weights=np.empty(0)):
    """
    Compute density using the triangle-shape-cloud algorithm. Assumes cubic box
    """
    gx = np.uint32(density.shape[0])
    gy = np.uint32(density.shape[1])
    gz = np.uint32(density.shape[2])
    threeD = gz != 1
    W = 1.
    Nw = len(weights)
    for n in range(len(positions)):
        # broadcast scalar weights
        if Nw == 1:
            W = weights[0]
        elif Nw > 1:
            W = weights[n]

        # convert to a position in the grid
        px = (positions[n,0]/boxsize)*gx # used to say boxsize+0.5
        py = (positions[n,1]/boxsize)*gy # used to say boxsize+0.5
        if threeD:
            pz = (positions[n,2]/boxsize)*gz # used to say boxsize+0.5

        # round to nearest cell center
        ix = np.int32(round(px))
        iy = np.int32(round(py))
        if threeD:
            iz = np.int32(round(pz))

        # calculate distance to cell center
        dx = ix - px
        dy = iy - py
        if threeD:
            dz = iz - pz

        # find the tsc weights for each dimension
        wx = .75 - dx**2
        wxm1 = .5*(.5 + dx)**2 # og not 1.5 cause wrt to adjacent cell
        wxp1 = .5*(.5 - dx)**2
        wy = .75 - dy**2
        wym1 = .5*(.5 + dy)**2
        wyp1 = .5*(.5 - dy)**2
        if threeD:
            wz = .75 - dz**2
            wzm1 = .5*(.5 + dz)**2
            wzp1 = .5*(.5 - dz)**2
        else:
            wz = 1.

        # find the wrapped x,y,z grid locations of the points we need to change
        # negative indices will be automatically wrapped
        ixm1 = rightwrap(ix - 1, gx)
        ixw  = rightwrap(ix    , gx)
        ixp1 = rightwrap(ix + 1, gx)
        iym1 = rightwrap(iy - 1, gy)
        iyw  = rightwrap(iy    , gy)
        iyp1 = rightwrap(iy + 1, gy)
        if threeD:
            izm1 = rightwrap(iz - 1, gz)
            izw  = rightwrap(iz    , gz)
            izp1 = rightwrap(iz + 1, gz)
        else:
            izw = np.uint32(0)

        # change the 9 or 27 cells that the cloud touches
        density[ixm1, iym1, izw ] += wxm1*wym1*wz  *W
        density[ixm1, iyw , izw ] += wxm1*wy  *wz  *W
        density[ixm1, iyp1, izw ] += wxm1*wyp1*wz  *W
        density[ixw , iym1, izw ] += wx  *wym1*wz  *W
        density[ixw , iyw , izw ] += wx  *wy  *wz  *W
        density[ixw , iyp1, izw ] += wx  *wyp1*wz  *W
        density[ixp1, iym1, izw ] += wxp1*wym1*wz  *W
        density[ixp1, iyw , izw ] += wxp1*wy  *wz  *W
        density[ixp1, iyp1, izw ] += wxp1*wyp1*wz  *W

        if threeD:
            density[ixm1, iym1, izm1] += wxm1*wym1*wzm1*W
            density[ixm1, iym1, izp1] += wxm1*wym1*wzp1*W

            density[ixm1, iyw , izm1] += wxm1*wy  *wzm1*W
            density[ixm1, iyw , izp1] += wxm1*wy  *wzp1*W

            density[ixm1, iyp1, izm1] += wxm1*wyp1*wzm1*W
            density[ixm1, iyp1, izp1] += wxm1*wyp1*wzp1*W

            density[ixw , iym1, izm1] += wx  *wym1*wzm1*W
            density[ixw , iym1, izp1] += wx  *wym1*wzp1*W

            density[ixw , iyw , izm1] += wx  *wy  *wzm1*W
            density[ixw , iyw , izp1] += wx  *wy  *wzp1*W

            density[ixw , iyp1, izm1] += wx  *wyp1*wzm1*W
            density[ixw , iyp1, izp1] += wx  *wyp1*wzp1*W

            density[ixp1, iym1, izm1] += wxp1*wym1*wzm1*W
            density[ixp1, iym1, izp1] += wxp1*wym1*wzp1*W

            density[ixp1, iyw , izm1] += wxp1*wy  *wzm1*W
            density[ixp1, iyw , izp1] += wxp1*wy  *wzp1*W

            density[ixp1, iyp1, izm1] += wxp1*wyp1*wzm1*W
            density[ixp1, iyp1, izp1] += wxp1*wyp1*wzp1*W
            

@njit(nogil=True, parallel=False)
def mean2d_numba_seq(tracks, bins, ranges, logk, weights=np.empty(0), dtype=np.float32):
    """
    Compute the mean number of modes per 2D bin.
    This implementation is 8-9 times faster than np.histogramdd and can be threaded (nogil!)
    """
    H = np.zeros((bins[0], bins[1]), dtype=np.float64)
    N = np.zeros((bins[0], bins[1]), dtype=np.float64)
    if logk:
        delta0 = 1/(np.log(ranges[0,1]/ranges[0,0]) / bins[0])
    else:
        delta0 = 1/((ranges[0,1] - ranges[0,0]) / bins[0])
    delta1 = 1/((ranges[1,1] - ranges[1,0]) / bins[1])
    Nw = len(weights)

    for t in range(tracks.shape[1]):
        #i = (tracks[0,t] - ranges[0,0]) * delta[0]
        if logk:
            i = np.log(tracks[0,t]/ranges[0,0]) * delta0
        else:
            i = (tracks[0,t] - ranges[0,0]) * delta0
        j = (tracks[1,t] - ranges[1,0]) * delta1
        if 0 <= i < bins[0] and 0 <= j < bins[1]:

            N[int(i),int(j)] += 1.
            H[int(i),int(j)] += weights[t]

    for i in range(bins[0]):
        for j in range(bins[1]):
            if N[i, j] > 0.:
                H[i, j] /= N[i, j]
    return H


def get_k_mu_box_edges(L_hMpc, n_xy, n_z, n_k_bins, n_mu_bins, k_hMpc_max, logk):
    """
    Compute the size of the k vector and mu for each mode and also bin edges for both. Assumes z direction is LOS
    """
    
    # this stores *all* Fourier wavenumbers in the box (no binning)
    k_box, mu_box = get_k_mu_box(L_hMpc, n_xy, n_z)
    k_box = k_box.flatten()
    mu_box = mu_box.flatten()

    # define k-binning (in 1/Mpc)
    lnk_max = np.log(k_hMpc_max)

    # set minimum k to make sure we cover fundamental mode
    e_tol = 1.e-4
    lnk_min = np.log((1.-e_tol)*np.min(k_box[k_box > 0.]))
    lnk_bin_max = lnk_max + (lnk_max-lnk_min)/(n_k_bins-1)
    if logk:
        lnk_bin_edges = np.linspace(lnk_min, lnk_bin_max, n_k_bins+1)
        k_bin_edges = np.exp(lnk_bin_edges)
    else:
        k_bin_edges = np.linspace(np.exp(lnk_min), np.exp(lnk_bin_max), n_k_bins+1)

    # define mu-binning
    mu_bin_edges = np.linspace(0., 1., n_mu_bins + 1)

    # get rid of k=0, mu=0 mode
    k_box = k_box[1:]
    mu_box = mu_box[1:]

    return k_box, mu_box, k_bin_edges, mu_bin_edges

def calc_pk3d(field, L_hMpc, k_box, mu_box, k_bin_edges, mu_bin_edges, logk):
    """
    Calculate the P3D for a given field (in h/Mpc units). Answer returned in (Mpc/h)^3 units
    """
    # get Fourier modes from skewers grid
    fourier_modes = np.fft.fftn(field) / field.size

    # get raw power
    raw_p3d = (np.abs(fourier_modes)**2).flatten()

    # get rid of k=0, mu=0 mode
    raw_p3d = raw_p3d[1:]

    # for the histograming
    ranges = ((k_bin_edges[0], k_bin_edges[-1]),(mu_bin_edges[0], mu_bin_edges[-1]))
    nbins2d = (len(k_bin_edges)-1, len(mu_bin_edges)-1)
    nbins2d = np.asarray(nbins2d).astype(np.int64)
    ranges = np.asarray(ranges).astype(np.float64)

    # power spectrum
    binned_p3d = mean2d_numba_seq(np.array([k_box, mu_box]), bins=nbins2d, ranges=ranges, logk=logk, weights=raw_p3d)

    # quantity above is dimensionless, multiply by box size (in Mpc/h)
    p3d_hMpc = binned_p3d * L_hMpc**3
    return p3d_hMpc


def calc_Pkmu(x1, y1, z1, nbins_k, nbins_mu, k_hMpc_max, logk, lbox, paste, num_cells, x2 = None, y2 = None, z2 = None):
    """
    Compute the 3D power spectrum given particle positions by first painting them on a mesh and then applying the fourier transforms and mode counting.
    """

    # assemble the positions and compute density field
    pos = np.vstack((x1, y1, z1)).T
    del x1, y1, z1; gc.collect()
    pos = pos.astype(np.float32)
    field = np.zeros((num_cells, num_cells, num_cells), dtype=np.float32)
    if paste == 'TSC':
        numba_tsc_3D(pos, field, lbox)
    elif paste == 'CIC':
        numba_cic_3D(pos, field, lbox)
    field /= (pos.shape[0]/num_cells**3.)
    field -= 1.
    del pos; gc.collect()

    # calculate power spectrum
    k_box, mu_box, k_bin_edges, mu_bin_edges = get_k_mu_box_edges(lbox, field.shape[0], field.shape[2], nbins_k, nbins_mu, k_hMpc_max, logk)
    pk3d = calc_pk3d(field, lbox, k_box, mu_box, k_bin_edges, mu_bin_edges, logk)
    del field; gc.collect()

    # define bin centers
    k_binc = (k_bin_edges[1:] + k_bin_edges[:-1])*.5
    mu_binc = (mu_bin_edges[1:] + mu_bin_edges[:-1])*.5
    return k_binc, mu_binc, pk3d
