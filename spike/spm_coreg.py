import numpy as np
import scipy.ndimage
import scipy.signal
import scipy.special
import scipy.optimize
import scipy.io
import nibabel
import sys
import os
import spm_hist2py

# root dir
PYPREPROCESS_DIR = os.path.dirname(
    os.path.split(os.path.abspath(__file__))[0])
sys.path.append(PYPREPROCESS_DIR)

import algorithms.registration.affine_transformations as affine_transformations
import algorithms.registration.kernel_smooth as kernel_smooth

EPS = np.finfo(float).eps

# def _paccuracy(V, p):
#     """Computes the accuracy limit of rounding intensities into uint8 type

#     """

#     if isinstance(V.dt[0], int):
#         acc = 0
#     else:
#         if V.pinfo.shape[1] == 1:
#             acc = np.abs(V.pinfo[0, 0])
#         else:
#             acc = np.abs(V.pinfo[0, p])

#     return acc


def loaduint8(filename):
    """Load data from file indicated by V into array of unsigned bytes.

    """

    if isinstance(filename, basestring):
        nii_img = nibabel.load(filename)
    else:
 # isinstance(filename, nibabel.Nifti1Image) or \
 #            isinstance(filename, nibabel.Nifti1Pair):
        nii_img = filename
        filename = filename.get_filename()
    # else:
    #     raise TypeError("Unsupported input type: %s" % type(filename))

    vol = nii_img.get_data()

    if vol.ndim == 4:
        vol = vol[..., 0]

    def _spm_slice_vol(p):
        """Gets data fir pth slice (place) of volume vol

        """

        return vol[..., p].copy()

    def _accumarray(subs, N):
        """Computes the frequency of each index in subs, extended as
        and array of length N

        """

        subs = np.array(subs)

        ac = np.zeros(N)

        for j in set(subs):
            ac[j] = len(np.nonzero(subs == j)[0])

        return ac

    def _progress_bar(msg):
        print(msg)

    # if len(V.pinfo.shape) == 1:
    #     V.pinfo = V.pinfo.reshape((-1, 1))

    # if V.pinfo.shape[1] == 1 and V.pinfo[0] == 2:
    #     mx = 0xFF * V.pinfo[0] + V.pinfo[1]
    #     mn = V.pinfo[1]
    # else:
    mx = -np.inf
    mn = np.inf
    _progress_bar("Computing min/max of %s..." % filename)
    for p in xrange(vol.shape[2]):
        img = _spm_slice_vol(p)
        # mx = max(img.max() + _paccuracy(V, p), mx)
        mx = max(img.max(), mx)
        mn = min(img.min(), mn)

    # another pass to find a maximum that allows a few hot-spots in the data
    nh = 2048
    h = np.zeros(nh)
    _progress_bar("2nd pass max/min of %s..." % filename)
    for p in xrange(vol.shape[2]):
        img = _spm_slice_vol(p)
        img = img[np.isfinite(img)]
        img = np.round((img + ((mx - mn) / (nh - 1) - mn)
                        ) * ((nh - 1) / (mx - mn)))
        h = h + _accumarray(img - 1, nh)

    tmp = np.hstack((np.nonzero(np.cumsum(h) / np.sum(h) > .9999)[0], nh))
    mx = (mn * nh - mx + tmp[0] * (mx - mn)) / (nh - 1)

    # load data from file indicated by V into an array of unsigned bytes
    uint8_dat = np.ndarray(vol.shape, dtype='uint8')
    print "Loading %s..." % filename
    for p in xrange(vol.shape[2]):
        img = _spm_slice_vol(p)

        # add white-noise before rounding to reduce aliasing artefact
        # acc = _paccuracy(V, p)
        acc = 0
        r = 0 if acc == 0 else np.random.randn(*img.shape) * acc

        # pth slice
        uint8_dat[..., p] = np.uint8(np.maximum(np.minimum(np.round((
                            img + r - mn) * (255. / (mx - mn))), 255.), 0.))

    # return the data
    return nibabel.Nifti1Image(uint8_dat, nii_img.get_affine())


def fwhm2sigma(fwhm):
    """Convert a FWHM value to sigma in a Gaussian kernel.

    Parameters
    ----------
    fwhm: array-like
       FWHM value or values

    Returns
    -------
    sigma: array or float
       sigma values corresponding to `fwhm` values

    Examples
    --------
    >>> sigma = fwhm2sigma(6)
    >>> sigmae = fwhm2sigma([6, 7, 8])
    >>> sigma == sigmae[0]
    True

    """

    fwhm = np.asarray(fwhm)

    return fwhm / np.sqrt(8 * np.log(2))


def smoothing_kernel(fwhm, x):
    # variance from fwhm
    s = fwhm ** 2 / (8 * np.log(2)) + EPS

    # Gaussian convolve with 0th degree B-spline
    w1 = .5 * np.sqrt(2 / s)
    w2 = -.5 / s
    w3 = np.sqrt(s / 2 / np.pi)
    krn = .5 * (scipy.special.erf(w1 * (x + 1)) * (x + 1) + scipy.special.erf(
            w1 * (x - 1)) * (x - 1) - 2 * scipy.special.erf(
            w1 * x) * x) + w3 * (np.exp(w2 * (x + 1) ** 2) + np.exp(
            w2 * (x - 1) ** 2) - 2 * np.exp(w2 * x ** 2))

    krn[krn < 0.] = 0

    return krn


def spm_conv_vol(vol, filtx, filty, filtz, xoff, yoff, zoff):
    output = scipy.ndimage.convolve1d(vol, filtx, axis=0)

    return output


def smooth_uint8(V, fwhm):
    """Convolves the volume V in memory (fwhm in voxels).

    """

    lim = np.ceil(2 * fwhm)

    x  = np.arange(-lim[0], lim[0] + 1)
    x = smoothing_kernel(fwhm[0], x)
    x  = x / np.sum(x)

    y  = np.arange(-lim[1], lim[1] + 1)
    y = smoothing_kernel(fwhm[1], y)
    y  = y / np.sum(y)

    z  = np.arange(-lim[2], lim[2] + 1)
    z = smoothing_kernel(fwhm[2], z)
    z  = z / np.sum(z)
    i  = (len(x) - 1) / 2
    j  = (len(y) - 1) / 2
    k  = (len(z) - 1) / 2

    return spm_conv_vol(V.astype('float'), x, y, z, -i, -j, -k)


# def _tpvd_interp(f, fshape, x, y, z):
#     """Performs "trilinear partial volume distribution" interpolation of a
#     gray-scale 3D image f, at a voxel (x, y, z).

#     Parameters
#     ----------
#     f: array_like of floats
#         gray-scale image to be interpolated
#     x: float or array_like of floats
#         ...

#     """

#     x = np.array(x)
#     y = np.array(y)
#     z = np.array(z)

#     # # use the map_coordinates(...) call below for faster execution
#     # return scipy.ndimage.map_coordinates(f.reshape(fshape, order='F'), [x, y, z], order=1,
#     #                                      mode='wrap',  # for SPM results
#     #                                      )

#     # XXX code below can/should be optimized, else we're dead!
#     ix = np.floor(x)
#     dx1 = x - ix
#     dx2 = 1.0 - dx1

#     iy = np.floor(y)
#     dy1 = y - iy
#     dy2 = 1.0 - dy1

#     iz = np.floor(z)
#     dz1 = z - iz
#     dz2 = 1.0 - dz1

#     offsets = np.array([ix[j] - 1 + fshape[0] * (iy[j] - 1 + fshape[1] * (
#                     iz[j] - 1)) for j in xrange(len(x))])

#     k222, k122, k212, k112 = np.array([
#             (f[offset], f[offset + 1], f[offset + fshape[0]],
#              f[offset + fshape[0] + 1]) for offset in offsets]).T

#     offsets = offsets + fshape[0] * fshape[1]

#     k221, k121, k211, k111 = np.array([
#             (f[offset], f[offset + 1], f[offset + fshape[0]],
#              f[offset + fshape[0] + 1]) for offset in offsets]).T

#     vf = (((k222 * dx2 + k122 * dx1) * dy2  +\
#                (k212 * dx2 + k112 * dx1) * dy1)) * dz2 +\
#                (((k221 * dx2 + k121 * dx1) * dy2 +\
#                      (k211 * dx2 + k111 * dx1) * dy1)) * dz1

#     return vf


# def _joint_histogram(g, f, M=None, gshape=None, fshape=None, s=[1, 1, 1]):
#     """
#     Computes the joint histogram of g and f[warp(f, M)],
#     where M is an affine transformation, and g and f are
#     3-dimensional images (scalars defined on the vertices of polytopes)
#     of possible different shapes (i.e different resolutions). The bins are
#     (256, 256) --i.e 8-bit gray-scale, so that the computed histogram is
#     a vector of length 65536.

#     Parameters
#     ----------
#     f: 3D array_like (or 1D Fortran-order ravelled version of)
#         3-dimensional image
#     g: 3D array_like (or 1D Fortran-order ravelled version of)
#         3-dimensional other image
#     M: array_like of shape (4, 4), optional (default None)
#         affine transformation with which f will be warped before
#         computing the histogram

#     Returns
#     -------
#     jh: joint histogram

#     """

#     # # everythx should be 8-bit gray-scale
#     # g = np.uint8(g)
#     # f = np.uint8(f)

#     # sanitize shapes
#     if gshape is None:
#         assert g.ndim == 3
#         gshape = g.shape
#     if fshape is None:
#         assert f.ndim == 3
#         fshape = f.shape

#     # table of magic numbers
#     ran = np.array([0.656619, 0.891183, 0.488144, 0.992646, 0.373326, 0.531378,
#                     0.181316, 0.501944, 0.422195, 0.660427, 0.673653, 0.95733,
#                     0.191866, 0.111216, 0.565054, 0.969166, 0.0237439,
#                     0.870216, 0.0268766, 0.519529, 0.192291, 0.715689,
#                     0.250673, 0.933865, 0.137189, 0.521622, 0.895202,
#                     0.942387, 0.335083, 0.437364, 0.471156, 0.14931, 0.135864,
#                     0.532498, 0.725789, 0.398703, 0.358419, 0.285279, 0.868635,
#                     0.626413, 0.241172, 0.978082, 0.640501, 0.229849, 0.681335,
#                     0.665823, 0.134718, 0.0224933, 0.262199, 0.116515,
#                     0.0693182, 0.85293, 0.180331, 0.0324186, 0.733926,
#                     0.536517, 0.27603, 0.368458, 0.0128863, 0.889206, 0.866021,
#                     0.254247, 0.569481, 0.159265, 0.594364, 0.3311, 0.658613,
#                     0.863634, 0.567623, 0.980481, 0.791832, 0.152594,
#                     0.833027, 0.191863, 0.638987, 0.669, 0.772088, 0.379818,
#                     0.441585, 0.48306, 0.608106, 0.175996, 0.00202556,
#                     0.790224, 0.513609, 0.213229, 0.10345, 0.157337, 0.407515,
#                     0.407757, 0.0526927, 0.941815, 0.149972, 0.384374,
#                     0.311059, 0.168534, 0.896648
#                     ])

#     # construct voxels of interest
#     rx = []
#     ry = []
#     rz = []
#     iran = 0  # index for ran table
#     z = 1.
#     while z < gshape[2] - s[2]:
#         y = 1.
#         while y < gshape[1] - s[1]:
#             x = 1.
#             while x < gshape[0] - s[0]:
#                 # print (x, y, z)
#                 iran = (iran + 1) % 97
#                 _rx  = x + ran[iran] * s[0]
#                 iran = (iran + 1) % 97
#                 _ry  = y + ran[iran] * s[1]
#                 iran = (iran + 1) % 97
#                 _rz  = z + ran[iran] * s[2]

#                 rx.append(_rx)
#                 ry.append(_ry)
#                 rz.append(_rz)

#                 # update x
#                 x += s[0]

#             # update y
#             y += s[1]

#         # update z
#         z += s[2]

#     rx, ry, rz = np.array([rx, ry, rz])

#     # rx, ry, rz = np.mgrid[:gshape[0] - s[0]:,
#     #                        :gshape[1] - s[1]:,
#     #                        :gshape[2] - s[2]:].reshape((3, -1))

#     # map voxels (rx, ry, rz) under the affine transformation
#     xp, yp, zp, _ = np.dot(M, [rx, ry, rz, np.ones(len(rx))])

#     # remove all voxel that have falling out of the FOV
#     fov_msk = ((zp >= 1.) & (zp < fshape[2]) & (yp >= 1.) &
#                (yp < fshape[1]) & (xp >= 1.) & (xp < fshape[0]))
#     rx = rx[fov_msk]
#     ry = ry[fov_msk]
#     rz = rz[fov_msk]
#     xp = xp[fov_msk]
#     yp = yp[fov_msk]
#     zp = zp[fov_msk]

#     # interpolate f at voxels (rx, ry, rz)
#     vf  = _tpvd_interp(f, fshape, xp, yp, zp)

#     # interpolate g at voxels (xp, yp, zp)
#     ivg = np.floor(_tpvd_interp(g, gshape, rx, ry, rz) + 0.5
#                    ).astype('int')
#     ivf = np.floor(vf).astype('int')

#     # camera ready: compute joint histogram
#     jh = np.zeros(256 * 256)  # 8-bit grayscale joint-histogram

#     for j in xrange(len(xp)):
#         # update corresponding bin
#         jh[(ivf[j] + ivg[j] * 256)] += (1 - (vf[j] - ivf[j]))

#         # handle special boundary
#         if ivg[j] < 255:
#             jh[(ivf[j] + 1 + ivg[j] * 256)] += (vf[j] - ivf[j])

#     # return joint histogram
#     return jh.reshape((256, 256), order='F')


def optfun(x, VG, VF, s=[1, 1, 1], cf='mi', fwhm=[7., 7.]):
    """
    Returns
    -------
    o

    """

    x = np.array(x)

    # voxel sizes
    vxg = np.sqrt(np.sum(VG.get_affine()[:3, :3] ** 2, axis=0))
    sg = s / vxg

    # create the joint histogram
    M = np.dot(scipy.linalg.lstsq(VF.get_affine(),
                                  affine_transformations.spm_matrix(x))[0],
                                  VG.get_affine())
    H = spm_hist2py.hist2py(M, VG.get_data(), VF.get_data(), sg)

    # Smooth the histogram
    lim  = np.ceil(fwhm * 2)
    krn1 = smoothing_kernel(fwhm[0], np.linspace(-1 * lim[0], lim[0],
                                                  num=2 * lim[0]))
    krn1 = krn1 / np.sum(krn1)
    krn2 = smoothing_kernel(fwhm[1], np.linspace(-1 * lim[1], lim[1],
                                                  num=2 * lim[1]))
    krn2 = krn2 / np.sum(krn2)

    # H = scipy.signal.sepfir2d(H, krn1, krn2)
    H = scipy.ndimage.gaussian_filter(H, sigma=fwhm2sigma(fwhm[:2]),
                                      mode='wrap')

    H = H + EPS
    sh = np.sum(H)
    H = H / sh
    s1 = np.sum(H, axis=0).reshape((-1, H.shape[0]), order='F')
    s2 = np.sum(H, axis=1).reshape((H.shape[1], -1), order='F')
    if cf == 'mi':
        # Mutual Information:
        H = H * np.log2(H / np.dot(s2, s1))
        mi = np.sum(H)
        o = -mi
    elif cf == 'ecc':
        # Entropy Correlation Coefficient of:
        # Maes, Collignon, Vandermeulen, Marchal & Suetens (1997).
        # "Multimodality image registration by maximisation of mutual
        # information". IEEE Transactions on Medical Imaging 16(2):187-198
        H = H * np.log2(H / np.dot(s2, s1))
        mi = np.sum(H.ravel(order='F'))
        ecc = -2 * mi / (np.sum(s1 * np.log2(s1)) + np.sum(s2 * np.log2(s2)))
        o = -ecc
    elif cf == 'nmi':
        # Normalised Mutual Information of:
        # Studholme,  Hill & Hawkes (1998).
        # "A normalized entropy measure of 3-D medical image alignment".
        # in Proc. Medical Imaging 1998, vol. 3338, San Diego, CA, pp. 132-143.
        nmi = (np.sum(s1 * np.log2(s1)) + np.sum(
                s2 * np.log2(s2))) / np.sum(np.sum(H * np.log2(H)))
        o = -nmi
    else:
        raise NotImplementedError("Unsupported cd: %s" % cf)

    return o


def spm_powell(x0, xi, tolsc, *otherargs):

    def of(x):

        output = optfun(x, *otherargs)

        # update progress bar
        token = "\t" + "   ".join(['%-8.4g' % z
                                     for z in x])
        token += " " * (len(x) * 12 - len(token)
                        ) + "| %.9g" % output
        print token

        return output

    def _cb(x):
        print "\r\n\t\tCurrent parameters estimate: %s\r\n" % x

    return scipy.optimize.fmin_powell(of, x0,
                                      direc=xi,
                                      xtol=min(np.min(tolsc), 1e-3),
                                      # callback=_cb
                                      )

from collections import namedtuple
Flags = namedtuple('Flags', 'fwhm sep cost_fun tol params')


def spm_coreg(ref_vol,
              src_vol,
              sep=[4, 2],
              params=[0, 0, 0, 0, 0, 0],
              tol=[.02, .02, .02, .001, .001, .001],
              cost_fun="nmi",
              fwhm=[7., 7., 7.],
              smooth_vols=True,
              ):
    """
    Similarity-based rigid-body multi-modal registration.

    Parameters
    ----------
    ref_vol: nibabel 3D image object
        reference (fixed) image
    src_vol: nibabel 3D image object
        source (moving) image
    sep: 1D array of floats, optional (default [4, 2])
        piramidal optimization seperation (in mm)
    params: 1D array of length 6, optional (default [0, 0, 0, 0, 0, 0]
        starting estimates
    cost_fun: string, optional (default "nmi")
        similarity function to be optimized. Possible values are:
        "mi": Mutual Information
        "nmi": Normalized Mutual Information
        "ecc": Entropy Correlation Coefficient
    tol: 1D array of 6 floats, optional (
    default [.02, .02, .02, .001, .001, .001])
        tolerances for the accuracy of each parameter

    Returns
    -------
    x: 1D array of 6 floats
        the six parameter defining the rigid-body motion needed to align the
        moving image `src_vol` with the reference image `ref_vol`

    """

    params = np.array(params)
    tol = np.array(tol)
    fwhm = np.array(fwhm)

    # get ready for spm_powell
    sc = tol
    sc = sc[:len(params)]
    xi = np.diag(sc * 20)

    # load ref_vol
    ref_vol = nibabel.load(ref_vol) if isinstance(
        ref_vol, basestring) else ref_vol
    if not ref_vol.get_data().dtype == np.uint8:
        ref_vol = loaduint8(ref_vol)

    # load src_vol
    src_vol = nibabel.load(src_vol) if isinstance(
        src_vol, basestring) else src_vol
    if not src_vol.get_data().dtype == np.uint8:
        src_vol = loaduint8(src_vol)

    # smooth vols
    if smooth_vols:
        vxg = np.sqrt(np.sum(ref_vol.get_affine()[:3, :3] ** 2, axis=0))
        fwhmg = np.sqrt(np.maximum(
                np.ones(3) * sep[-1] ** 2 - vxg ** 2, [0, 0, 0])) / vxg
        ref_vol = nibabel.Nifti1Image(
            scipy.ndimage.gaussian_filter(ref_vol.get_data(),
                                          fwhm2sigma(fwhmg)),
            ref_vol.get_affine())

        vxf = np.sqrt(np.sum(src_vol.get_affine()[:3, :3] ** 2, axis=0))
        fwhmf = np.sqrt(np.maximum(
                np.ones(3) * sep[-1] ** 2 - vxf ** 2, [0, 0, 0])) / vxf
        src_vol = nibabel.Nifti1Image(
            scipy.ndimage.gaussian_filter(src_vol.get_data(),
                                          fwhm2sigma(fwhmf)),
            src_vol.get_affine())

    # piramidal loop
    xk = list(params)
    for samp in sep:
        # powell gradient-less local optimization
        xk = spm_powell(xk, xi, sc, ref_vol, src_vol, samp, cost_fun, fwhm)

    return xk


if __name__ == '__main__':
    import matplotlib.pyplot as plt
    from external.nilearn.datasets import fetch_spm_auditory_data
    from algorithms.registration.spm_realign import _apply_realignment_to_vol

    sd = fetch_spm_auditory_data(os.path.join(os.environ['HOME'],
                                              "CODE/datasets/spm_auditory"))

    fig = plt.figure()

    flags = Flags(fwhm=np.array([7., 7., 7.]),
                  sep=np.array([4, 2]),
                  cost_fun='nmi',
                  tol=np.array([.02] * 3 + [.001] * 3),
                  params=np.zeros(6))

    import scipy.io
    toto = scipy.io.loadmat(os.path.join(PYPREPROCESS_DIR,
                                         "test_data/spm_hist2_args_1.mat"),
                            squeeze_me=True, struct_as_record=False)
    VG, VFk = [toto[k] for k in ['VG', 'VFk']]
    VG = nibabel.Nifti1Image(VG.uint8, VG.mat)
    VFk = nibabel.Nifti1Image(VFk.uint8, VFk.mat)

    # q0 = spm_coreg(VG, VFk, smooth_vols=False, **flags.__dict__)
    # VFk = _apply_realignment_to_vol(VFk, q0)
    # print q0

    shape = (6, 2)
    p = np.zeros(6)
    for i, j in np.ndindex(shape):
        p[:3] = np.random.randn(3) * (i + j) * 2
        p[3:] = np.random.randn(3) * .01

        x = _apply_realignment_to_vol(sd.func[0], p)
        q = spm_coreg(sd.func[0], x)

        ax = plt.subplot2grid(shape, (i, j))
        ax.plot(np.transpose([p, -q]), 's-')

    plt.show()
