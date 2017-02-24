from pyraf import iraf
from astropy.io import fits
import os
import numpy as np
import scipy
import statsmodels.api as stats


def register_images(science_fname, reference_fname):
    """Register reference image to science image using wcstools remap"""

    output_fname = science_fname.replace('.fit', '.ref.fit')
    os.system('remap -f {0} -o {1} {2}'.format(science_fname, output_fname, reference_fname))

    return output_fname


def center_psf(psf):
    """Center psf at (0,0)"""

    psf = np.roll(psf, psf.shape[0] / 2, 0)
    psf = np.roll(psf, psf.shape[1] / 2, 1)
    return psf


def convert_to_background_fft(gain, background_fft, science_background_std, reference_background_std):
    """Convert params in fourier space to image space"""

    return background_fft * np.sqrt(science_background_std ** 2 + gain ** 2 * reference_background_std ** 2)


def fit_noise(data, fit_type='gaussian', n_stamps=1):
    """Find the standard deviation of the image background; returns standard deviation, median"""

    if fit_type == 'gaussian':
        median_small = np.zeros([n_stamps, n_stamps])
        std_small = np.zeros([n_stamps, n_stamps])
        for y_stamp in range(n_stamps):
            for x_stamp in range(n_stamps):
                y_index = [y_stamp * data.shape[0] / n_stamps, (y_stamp + 1) * data.shape[0] / n_stamps]
                x_index = [x_stamp * data.shape[1] / n_stamps, (x_stamp + 1) * data.shape[1] / n_stamps]
                stamp_data = data[y_index[0]: y_index[1], x_index[0]: x_index[1]]
                trimmed_stamp_data = stamp_data[stamp_data < np.percentile(stamp_data, 90)]
                trimmed_stamp_data = trimmed_stamp_data[trimmed_stamp_data != 0]
                histogram_data = np.histogram(trimmed_stamp_data, bins=100)
                x = histogram_data[1][:-1]
                y = histogram_data[0]
                guess = [np.max(y), np.median(trimmed_stamp_data), np.std(trimmed_stamp_data)]
                parameters, covariance = scipy.optimize.curve_fit(gauss, x, y, p0=guess, maxfev=1600)
                median_small[y_stamp, x_stamp] = parameters[1]
                std_small[y_stamp, x_stamp] = parameters[2]

        median = scipy.ndimage.zoom(median_small, [data.shape[0] / float(n_stamps), data.shape[1] / float(n_stamps)])
        std = scipy.ndimage.zoom(std_small, [data.shape[0] / float(n_stamps), data.shape[1] / float(n_stamps)])

    elif fit_type == 'simple':
        std = np.std(data) * np.ones(data.shape)
        median = np.median(data) * np.ones(data.shape)

    return std, median


def fit_psf(image_file, fwhm=5., noise=30., verbose=True, show=False, max_count=15000.):
    """Fit the PSF given an image file name"""

    if verbose:
        verb = 'yes'
    else:
        verb = 'no'

    psf_is_good = False

    image = image_file
    coords_file = image + '.coo'
    mag_file = image + '.mag'
    psft_file = image + '.pst'
    psf_file = image + '.psf'
    opst_file = image + '.opst'
    group_file = image + '.group'
    see_file = image + '.see'

    while not psf_is_good:
        delete_list = [psf_file + '.fits', coords_file, mag_file, psft_file, opst_file, group_file, see_file]
        for item in delete_list:
            try:
                os.remove(item)
            except OSError:
                pass


        try:
            # generate star catalog using daofind
            iraf.noao()
            iraf.digiphot()
            iraf.daophot(_doprint=0)
            iraf.datapars.datamax = max_count
            iraf.datapars.sigma = noise
            iraf.findpars.threshold = 5.
            iraf.datapars.datamin = 0
            iraf.datapars.datamax = max_count
            iraf.datapars.fwhm = fwhm
            iraf.daofind(image_file, output=coords_file, verify='no', display='no', verbose=verb)

            # uncomment the raw_input line if daofind adds stars that do not exist in catalog
            # this gives you time to manually remove nonexistent stars that cause a bad psf fit
            # this is temporary until daofind works better with images coadded with swarp
            # raw_input('Manually edit .coo file now if necessary; Press enter to continue ')

            # do aperture photometry
            a1, a2, a3, a4, = int(fwhm + 0.5), int(fwhm * 2 + 0.5), int(fwhm * 3 + 0.5), int(fwhm * 4 + 0.5)
            iraf.photpars.apertures = '{0},{1},{2}'.format(a2, a3, a4)
            iraf.centerpars.calgori = 'centroid'
            iraf.fitskypars.salgori = 'mode'
            iraf.fitskypars.annulus = 10
            iraf.fitskypars.dannulu = 10
            iraf.phot(image_file, coords_file, mag_file, verify='no', verbose=verb)

            # select PSF stars
            iraf.daopars.fitrad = a1
            iraf.daopars.nclean = 4
            iraf.daopars.varorder = 0
            iraf.daopars.recenter = 'yes'
            iraf.pstselect(image_file, mag_file, psft_file, maxnpsf=50, verify='no', verbose=verb)

            # make PSF
            iraf.psf(image_file, mag_file, psft_file, psf_file, opst_file, group_file,
                     verify='no', verbose=verb, interactive='no')

            # show psf to user for approval
            if show:
                iraf.seepsf(psf_file, see_file)
                iraf.surface(see_file)
                psf_is_goodyn = raw_input('GoodPSF? y/n: ')
                if psf_is_goodyn == 'y':
                    psf_is_good = True
                else:
                    fwhmguess = raw_input('New fwhm: [{}] '.format(fwhm))
                    noiseguess = raw_input('New noise: [{}] '.format(noise))
                    if fwhmguess != '':
                        fwhm = float(fwhmguess)
                    if noiseguess != '':
                        noise = float(noiseguess)

            else:
                break

        except:
            if show:
                print 'PSF fitting failed; try again with different parameters'
                fwhm = float(raw_input('New fwhm: '))
                noise = float(raw_input('New noise: '))
            else:
                print 'Unable to fit with given parameters'
                break
    print 'Saved to {}'.format(psf_file)

    delete_list = [coords_file, mag_file, psft_file, opst_file, group_file, see_file]
    for item in delete_list:
        try:
            os.remove(item)
        except OSError:
            pass

    return psf_file


def gauss(x, a, b, c):
    """Return a gaussian function"""

    return a * np.exp(-(x-b)**2/(2*c**2))


def interpolate_bad_pixels(image, mask, method='median_filter', median_size=6):
    """Interpolate over bad pixels using a global median"""

    if method == 'median':
        interpolated_image = np.copy(image)
        interpolated_image[mask == 1] = np.median(image)

    elif method == 'mesh':
        # taken from http://stackoverflow.com/questions/37662180/interpolate-missing-values-2d-python
        image[mask == 1] = np.NaN
        x = np.arange(0, image.shape[1])
        y = np.arange(0, image.shape[0])
        image = np.ma.masked_invalid(image)
        xx, yy = np.meshgrid(x, y)
        x1 = xx[~image.mask]
        y1 = yy[~image.mask]
        new_image = image[~image.mask]
        interpolated_image = scipy.interpolate.griddata((x1, y1), new_image.ravel(), (xx, yy), method='cubic')

    elif method == 'median_filter':
        # taken from http://stackoverflow.com/questions/18951500/automatically-remove-hot-dead-pixels-from-an-image-in-python
        pix = np.transpose(np.where(mask == 1))
        blurred = scipy.ndimage.median_filter(image, size=median_size)
        interpolated_image = np.copy(image)
        for y, x in pix:
            interpolated_image[y, x] = blurred[y, x]

    return interpolated_image


def get_saturation_count(image_filename):
    """Get the saturation count from the header of an image"""

    image_header = fits.getheader(image_filename)
    try:
        saturation_count = image_header['saturate']
    except KeyError:
        saturation_count = 10e9

    try:
        maxlin_count = image_header['maxlin']
    except KeyError:
        maxlin_count = 10e9

    pixel_upper_limit = np.min([saturation_count, maxlin_count])

    return pixel_upper_limit


def make_pixel_mask(image, saturation_count):
    """Make a mask of saturated pixels"""

    pixel_mask = np.copy(image)
    pixel_mask[image >= saturation_count] = 1
    pixel_mask[image < saturation_count] = 0

    return pixel_mask


def read_psf_file(psf_filename):
    """Extract normalized psf array from iraf psf file"""

    iraf.noao()
    iraf.digiphot()
    iraf.daophot(_doprint=0)
    iraf.seepsf(psf_filename, 'temp.psf.fits')
    psf = fits.open('temp.psf.fits')[0].data
    psf /= np.sum(psf)
    os.system('rm temp.psf.fits')
    return psf


def remove_bad_pix(data, saturation=None, remove_background_pix=True, significance=1.):
    """Remove saturated and background pixels from dataset"""

    if saturation is not None:
        not_saturated_pix = np.where(data < saturation)
    else:
        not_saturated_pix = np.arange(data.size)

    if remove_background_pix:
        threshold = np.median(data) + significance * np.std(data)
        signal_pix = np.where(data > threshold)
    else:
        signal_pix = np.arange(data.size)

    good_pix_in_common = np.intersect1d(not_saturated_pix, signal_pix)
    return good_pix_in_common


def resize_psf(psf, shape):
    """Resize centered (0,0) psf to larger shape"""

    psf_extended = np.zeros(shape)
    center = [psf_extended.shape[0] / 2, psf_extended.shape[1] / 2]
    stamp = psf.shape
    vert_offsets = [center[0] - stamp[0] / 2, center[0] + stamp[0] / 2]
    horiz_offsets = [center[1] - stamp[1] / 2, center[1] + stamp[1] / 2]
    psf_extended[vert_offsets[0]: vert_offsets[1] + 1, horiz_offsets[0]: horiz_offsets[1] + 1] = psf
    return psf_extended


def solve_iteratively(science, reference, n_stamps=1):
    """Solve for linear fit iteratively"""

    shape = science.image_data.shape
    gain_small = np.zeros([n_stamps, n_stamps])
    background_small = np.zeros([n_stamps, n_stamps])

    gain_tolerance = 1e-8
    background_fft_tolerance = 1e-8
    max_iterations = 10

    science_psf = center_psf(resize_psf(science.raw_psf_data, np.array(shape) / n_stamps))
    reference_psf = center_psf(resize_psf(reference.raw_psf_data, np.array(shape) / n_stamps))

    for y_stamp in range(n_stamps):
        for x_stamp in range(n_stamps):
            i = 0
            gain = 1.
            background_fft = 0.
            gain0 = 10e5
            background_fft0 = 10e5

            y_index = [y_stamp * shape[0] / n_stamps, (y_stamp + 1) * shape[0] / n_stamps]
            x_index = [x_stamp * shape[1] / n_stamps, (x_stamp + 1) * shape[1] / n_stamps]

            science_image = science.image_data[y_index[0]: y_index[1], x_index[0]: x_index[1]]
            reference_image = reference.image_data[y_index[0]: y_index[1], x_index[0]: x_index[1]]

            science_image_fft = np.fft.fft2(science_image)
            reference_image_fft = np.fft.fft2(reference_image)
            science_psf_fft = np.fft.fft2(science_psf)
            reference_psf_fft = np.fft.fft2(reference_psf)

            science_background_std = science.background_std[y_index[0]: y_index[1], x_index[0]: x_index[1]]
            reference_background_std = reference.background_std[y_index[0]: y_index[1], x_index[0]: x_index[1]]

            while abs(gain - gain0) > gain_tolerance or abs(background_fft - background_fft0) > background_fft_tolerance:

                denominator = science_background_std ** 2 * abs(reference_psf_fft) ** 2
                denominator += gain ** 2 * reference_background_std ** 2 * abs(science_psf_fft) ** 2
                science_convolved_image_fft = reference_psf_fft * science_image_fft / np.sqrt(denominator)
                reference_convolved_image_fft = science_psf_fft * reference_image_fft / np.sqrt(denominator)
                science_convolved_image = np.real(np.fft.ifft2(science_convolved_image_fft))
                reference_convolved_image = np.real(np.fft.ifft2(reference_convolved_image_fft))
                science_convolved_image_flatten = science_convolved_image.flatten()
                reference_convolved_image_flatten = reference_convolved_image.flatten()

                # remove bad pixels
                science_good_pix = remove_bad_pix(science_convolved_image_flatten, saturation=science.saturation_count)
                reference_good_pix = remove_bad_pix(reference_convolved_image_flatten, saturation=reference.saturation_count)
                good_pix_in_common = np.intersect1d(science_good_pix, reference_good_pix)
                science_convolved_image_flatten = science_convolved_image_flatten[good_pix_in_common]
                reference_convolved_image_flatten = reference_convolved_image_flatten[good_pix_in_common]

                #import matplotlib.pyplot as plt
                #plt.plot(science_convolved_image_flatten, reference_convolved_image_flatten, 'bo')
                #plt.show()

                gain0, background_fft0 = gain, background_fft

                x = stats.add_constant(reference_convolved_image_flatten)
                y = science_convolved_image_flatten
                robust_fit = stats.RLM(y, x).fit()
                parameters = robust_fit.params
                stamp_gain = parameters[1]
                background_fft = parameters[0]

                if i == max_iterations:
                    break
                i += 1
                print('Iteration {}:'.format(i))
                stamp_background = convert_to_background_fft(gain, background_fft, science_background_std, reference_background_std)
                print('Beta = {0}, background = {1}'.format(gain, np.median(stamp_background)))

            #print('Fit done in {} iterations'.format(i))

            covariance = robust_fit.bcov_scaled[0, 0]
            stamp_background = convert_to_background_fft(stamp_gain, background_fft, science_background_std, reference_background_std)

            print('Beta = ' + str(stamp_gain))
            print('Gamma = ' + str(np.median(stamp_background)))
            #print('Beta Variance = ' + str(covariance))
            gain_small[y_stamp, x_stamp] = stamp_gain
            background_small[y_stamp, x_stamp] = np.median(stamp_background)

    gain = scipy.ndimage.zoom(gain_small, [shape[0] / float(n_stamps), shape[1] / float(n_stamps)])
    background = scipy.ndimage.zoom(background_small, [shape[0] / float(n_stamps), shape[1] / float(n_stamps)])


    return gain, background
