import numpy as np
import matplotlib.pyplot as plt
import py4DSTEM
import scipy.io as sio
from py4DSTEM.process.utils import print_progress_bar
from py4DSTEM.process.utils import polar_elliptical_transform
from py4DSTEM.process.utils.ellipticalCoords import *
import matplotlib
from tqdm import tqdm

# this fixes figure sizes on HiDPI screens
matplotlib.rcParams["figure.dpi"] = 200
plt.ion()


def fit_stack(datacube, init_coefs, mask=None):
    """
    This will fit an ellipse using the polar elliptical transform code to all the diffraction patterns. It will take in a datacube and return a coefficient array which can then be used to map strain, fit the centers, etc.

    Accepts:
        datacute    - a datacube of diffraction data
        init_coefs  - an initial starting guess for the fit
        mask        - a mask, either 2D or 4D, for either one mask for the whole stack, or one per pattern. 
    Returns:
        coef_cube  - an array of coefficients of the fit
    """
    coefs_array = np.zeros([i for i in datacube.data.shape[0:2]] + [len(init_coefs)])
    for i in tqdm(range(datacube.R_Nx)):
        for j in tqdm(range(datacube.R_Ny)):
            if len(mask.shape) == 2:
                mask_current = mask
            elif len(mask.shape) == 4:
                mask_current = mask[i, j, :, :]

            coefs = fit_double_sided_gaussian(
                datacube.data[i, j, :, :], init_coefs, mask=mask_current
            )
            coefs_array[i, j] = coefs

    return coefs_array


def calculate_coef_strain(coef_cube, r_ref):
    """
    This function will calculate the strains from a 3D matrix output by fit_stack

    Coefs order:
        I0          the intensity of the first gaussian function
        I1          the intensity of the Janus gaussian
        sigma0      std of first gaussian
        sigma1      inner std of Janus gaussian
        sigma2      outer std of Janus gaussian
        c_bkgd      a constant offset
        R           center of the Janus gaussian
        x0,y0       the origin
        B,C         1x^2 + Bxy + Cy^2 = 1

    Accepts:
        coef_cube   - output from fit_stack
        r_ref       - a reference 0 strain radius - needed because we fit r as well as B and C
    Returns:
        exx         - strain in the x axis direction in image coordinates
        eyy         - strain in the y axis direction in image coordinates
        exy         - shear

    """
    R = coef_cube[:, :, 6]
    r_ratio = (
        R / r_ref
    )  # this is a correction factor for what defines 0 strain, and must be applied to A, B and C. This has been found _experimentally_! TODO have someone else read this

    A = 1 / r_ratio ** 2
    B = coef_cube[:, :, 9] / r_ratio ** 2
    C = coef_cube[:, :, 10] / r_ratio ** 2

    exx, eyy, exy = np.empty_like(A), np.empty_like(C), np.empty_like(B)

    for i in range(A.shape[0]):
        for j in range(A.shape[1]):
            m_ellipse = np.asarray([[A[i, j], B[i, j] / 2], [B[i, j] / 2, C[i, j]]])
            e_vals, e_vecs = np.linalg.eig(m_ellipse)
            ang = np.arctan2(e_vecs[1, 0], e_vecs[0, 0])
            rot_matrix = np.asarray(
                [[np.cos(ang), -np.sin(ang)], [np.sin(ang), np.cos(ang)]]
            )
            transformation_matrix = np.diag(np.sqrt(e_vals))
            transformation_matrix = rot_matrix @ transformation_matrix @ rot_matrix.T

            exx[i, j] = transformation_matrix[0, 0] - 1
            eyy[i, j] = transformation_matrix[1, 1] - 1
            exy[i, j] = 0.5 * (
                transformation_matrix[0, 1] + transformation_matrix[1, 0]
            )

    return exx, eyy, exy


def plot_strains(strains, cmap="RdBu_r", vmin=None, vmax=None, mask=None):
    """
    This function will plot strains with a unified color scale.

    Accepts:
        strains             - a collection of 3 arrays in the format (exx, eyy, exy)
        cmap, vmin, vmax    - imshow parameters
        mask                - real space mask of values not to show (black)
    """
    cmap = matplotlib.cm.get_cmap(cmap)
    if vmin is None:
        vmin = np.min(strains)
    if vmax is None:
        vmax = np.max(strains)
    if mask is None:
        mask = np.ones_like(strains[0])
    else:
        cmap.set_under("black")
        cmap.set_over("black")
        cmap.set_bad("black")

    mask = mask.astype(bool)

    for i in strains:
        i[mask] = np.nan

    plt.figure(88, figsize=(9, 5.8), clear=True)
    f, (ax1, ax2, ax3) = plt.subplots(1, 3, num=88)
    ax1.imshow(strains[0], cmap=cmap, vmin=vmin, vmax=vmax)
    ax1.tick_params(
        axis="both",
        which="both",
        bottom=False,
        top=False,
        left=False,
        right=False,
        labelbottom=False,
        labelleft=False,
    )
    ax1.set_title(r"$\epsilon_{xx}$")

    ax2.imshow(strains[1], cmap=cmap, vmin=vmin, vmax=vmax)
    ax2.tick_params(
        axis="both",
        which="both",
        bottom=False,
        top=False,
        left=False,
        right=False,
        labelbottom=False,
        labelleft=False,
    )
    ax2.set_title(r"$\epsilon_{yy}$")

    im = ax3.imshow(strains[2], cmap=cmap, vmin=vmin, vmax=vmax)
    ax3.tick_params(
        axis="both",
        which="both",
        bottom=False,
        top=False,
        left=False,
        right=False,
        labelbottom=False,
        labelleft=False,
    )
    ax3.set_title(r"$\epsilon_{xy}$")

    cbar_ax = f.add_axes([0.125, 0.25, 0.775, 0.05])
    f.colorbar(im, cax=cbar_ax, orientation="horizontal")

    return


def compute_polar_symmetries(dp):
    """
    This function will take in a polar transformed diffraction pattern (2D), compute the autocorrelation, and then the symmetries as well. 

    This function is to be used by the function which does this for the whole stack. 

    dp has theta along axis 0, and r along axis 1

    the normalized fourier coeffiecent for a certain symmetry order, a measure of symmetry, is then found by taking the average of the result in the radial bins desired. For example, two fold symmetry over the first five radial bins is equivalent to np.mean(dp_fft_normalized[2, 0:5])
    """
    dp_autocorrelated = np.fft.ifft(
        np.abs(np.fft.fft(dp, axis=0)) ** 2, axis=0
    )  # this emphasizes signal, but destroys any angular info
    dp_fft = np.abs(np.fft.fft(dp_autocorrelated, axis=0))
    # removes the effect of changing pattern intensity
    dp_fft_normalized = dp_fft / dp_fft[0, :]

    return dp_fft_normalized


def compute_polar_stack_symmetries(datacube_polar):
    """
    This function will take in a datacube of polar-transformed diffraction patterns, and do the autocorrelation, before taking the fourier transform along the theta direction, such that symmetries can be measured. They will be plotted by a different function

    Accepts:
        datacube_polar  - diffraction pattern cube that has been polar transformed

    Returns:
        datacube_symmetries - the normalized fft along the theta direction of the autocorrelated patterns in datacube_polar
    """
    datacube_symmetries = np.empty_like(datacube_polar.data)

    for i in tqdm(range(datacube_polar.R_Nx)):
        for j in range(datacube_polar.R_Ny):
            datacube_symmetries[i, j, :, :] = compute_polar_symmetries(
                datacube_polar.data[i, j, :, :]
            )

    return datacube_symmetries


def corr2d(im1, im2, mask=None):
    """
    This is the python version of matlab's corr2
    """

    if mask is not None:
        im1 = im1[mask]
        im2 = im2[mask]

    corr_val = np.sum((im1 - im1.mean()) * (im2 - im2.mean())) / np.sqrt(
        np.sum((im1 - im1.mean()) ** 2) * np.sum((im2 - im2.mean()) ** 2)
    )

    return corr_val


def compute_nn_corr(datacube, mask=None):
    """        
    the datacube is just a numpy array, and mask as well

    we will ignore the outer boundary where nearer neighbors aren't computed
    """
    corr_result = np.empty(datacube.shape[0:2])
    corr_result = corr_result[1:-1, 1:-1]

    for i in tqdm(range(corr_result.shape[0])):
        for j in range(corr_result.shape[1]):
            corr_result[i, j] = np.mean(
                [
                    corr2d(
                        datacube[i + 1, j + 1, :, :], 
                        datacube[i, j, :, :], 
                        mask=mask,
                    ),
                    corr2d(
                        datacube[i + 1, j + 1, :, :],
                        datacube[i, j + 1, :, :],
                        mask=mask,
                    ),
                    corr2d(
                        datacube[i + 1, j + 1, :, :],
                        datacube[i, j + 2, :, :],
                        mask=mask,
                    ),
                    corr2d(
                        datacube[i + 1, j + 1, :, :],
                        datacube[i + 1, j, :, :],
                        mask=mask,
                    ),
                    corr2d(
                        datacube[i + 1, j + 1, :, :],
                        datacube[i + 2, j + 2, :, :],
                        mask=mask,
                    ),
                    corr2d(
                        datacube[i + 1, j + 1, :, :],
                        datacube[i + 2, j, :, :],
                        mask=mask,
                    ),
                    corr2d(
                        datacube[i + 1, j + 1, :, :],
                        datacube[i + 2, j + 1, :, :],
                        mask=mask,
                    ),
                    corr2d(
                        datacube[i + 1, j + 1, :, :],
                        datacube[i + 2, j + 2, :, :],
                        mask=mask,
                    ),
                ]
            )

    return corr_result


def plot_symmetries(datacube_symmetries, sym_order, r_range):
    """
    This function will take in a datacube from compute_polar_stack_symmetries and plot a specific symmetry order. 

    Accepts:
        datacube_symmetries - result of compute_polar_stack_symmetries, the stack of fft'd autocorrelated diffraction patterns. This is just a 4D numpy array
        sym_order           - symmetry order desired to plot
        r_range             - tuple of r indexes to sum/avg over, indicating start, and stop
    Returns:
        None
    """
    plt.figure(f"Symmetry order {sym_order}", clear=True)
    plt.imshow(
        np.mean(datacube_symmetries[:, :, sym_order, r_range[0] : r_range[1]], axis=2)
    )

    return None


def plot_nn(datacube, i, j, mask=None):
    """
    this will just plot a 3x3 grid of patterns
    datacube is a numpy array
    i is row,
    j is column
    mask is a numpy array
    """
    if mask is None:
        mask = np.ones(datacube.shape[2:4]).astype(bool)

    p = plt.figure(f"Nearest Neighbors of {i}, {j}", clear=True)
    tiled_image = np.concatenate(
        np.concatenate(datacube[i - 1 : i + 2, j - 1 : j + 2, :, :] * mask, axis=-2),
        axis=-1,
    )
    plt.imshow(tiled_image)

    return None
