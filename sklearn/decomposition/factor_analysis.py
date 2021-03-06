"""Factor Analysis.
A latent linear variable model, similar to ProbabilisticPCA.

This implementation is based on David Barber's Book,
Bayesian Reasoning and Machine Learning,
http://www.cs.ucl.ac.uk/staff/d.barber/brml,
Algorithm 21.1
"""

# Author: Christian Osendorfer <osendorf@gmail.com>
#         Alexandre Gramfort <alexandre.gramfort@inria.fr>
#         Denis A. Engemann <d.engemann@fz-juelich.de>

# Licence: BSD3

import warnings
from math import sqrt, log
import numpy as np
from scipy import linalg


from ..base import BaseEstimator, TransformerMixin
from ..externals.six.moves import xrange
from ..utils import array2d, check_arrays, check_random_state
from ..utils.extmath import fast_logdet, fast_dot, randomized_svd
from ..utils import ConvergenceWarning


class FactorAnalysis(BaseEstimator, TransformerMixin):
    """Factor Analysis (FA)

    A simple linear generative model with Gaussian latent variables.

    The observations are assumed to be caused by a linear transformation of
    lower dimensional latent factors and added Gaussian noise.
    Without loss of generality the factors are distributed according to a
    Gaussian with zero mean and unit covariance. The noise is also zero mean
    and has an arbitrary diagonal covariance matrix.

    If we would restrict the model further, by assuming that the Gaussian
    noise is even isotropic (all diagonal entries are the same) we would obtain
    :class:`PPCA`.

    FactorAnalysis performs a maximum likelihood estimate of the so-called
    `loading` matrix, the transformation of the latent variables to the
    observed ones, using expectation-maximization (EM).

    Parameters
    ----------
    n_components : int | None
        Dimensionality of latent space, the number of components
        of ``X`` that are obtained after ``transform``.
        If None, n_components is set to the number of features.

    tol : float
        Stopping tolerance for EM algorithm.

    copy : bool
        Whether to make a copy of X. If ``False``, the input X gets overwritten
        during fitting.

    max_iter : int
        Maximum number of iterations.

    verbose : int | bool
        Print verbose output.

    noise_variance_init : None | array, shape=(n_features,)
        The initial guess of the noise variance for each feature.
        If None, it defaults to np.ones(n_features)

    svd_method : {'lapack', 'randomized'}
        Which SVD method to use. If 'lapack' use standard SVD from
        scipy.linalg, if 'randomized' use fast ``randomized_svd`` function.
        Defaults to 'randomized'. For most applications 'randomized' will
        be sufficiently precise while providing significant speed gains.
        Accuracy can also be improved by setting higher values for
        `iterated_power`. If this is not sufficient, for maximum precision
        you should choose 'lapack'.

    iterated_power : int, optional
        Number of iterations for the power method. 3 by default. Only used
        if ``svd_method`` equals 'randomized'

    random_state : int or RandomState
        Pseudo number generator state used for random sampling. Only used
        if ``svd_method`` equals 'randomized'

    Attributes
    ----------
    `components_` : array, [n_components, n_features]
        Components with maximum variance.

    `loglike_` : list, [n_iterations]
        The log likelihood at each iteration.

    `noise_variance_` : array, shape=(n_features,)
        The estimated noise variance for each feature.

    References
    ----------
    .. David Barber, Bayesian Reasoning and Machine Learning,
        Algorithm 21.1

    .. Christopher M. Bishop: Pattern Recognition and Machine Learning,
        Chapter 12.2.4

    See also
    --------
    PCA: Principal component analysis, a similar non-probabilistic
        model model that can be computed in closed form.
    ProbabilisticPCA: probabilistic PCA.
    FastICA: Independent component analysis, a latent variable model with
        non-Gaussian latent variables.
    """
    def __init__(self, n_components=None, tol=1e-2, copy=True, max_iter=1000,
                 verbose=0, noise_variance_init=None, svd_method='randomized',
                 iterated_power=3, random_state=0):
        self.n_components = n_components
        self.copy = copy
        self.tol = tol
        self.max_iter = max_iter
        if svd_method not in ['lapack', 'randomized']:
            raise ValueError('SVD method %s is not supported. Please consider'
                             ' the documentation' % svd_method)
        self.svd_method = svd_method
        if verbose:
            warnings.warn('The `verbose` parameter has been deprecated and '
                          'will be removed in 0.16. To reduce verbosity '
                          'silence Python warnings instead.',
                          DeprecationWarning)

        self.verbose = verbose
        self.noise_variance_init = noise_variance_init
        self.iterated_power = iterated_power
        self.random_state = random_state

    def fit(self, X, y=None):
        """Fit the FactorAnalysis model to X using EM

        Parameters
        ----------
        X : array-like, shape (n_samples, n_features)
            Training data.

        Returns
        -------
        self
        """
        X = array2d(check_arrays(X, copy=self.copy, sparse_format='dense',
                    dtype=np.float)[0])

        n_samples, n_features = X.shape
        n_components = self.n_components
        if n_components is None:
            n_components = n_features
        self.mean_ = np.mean(X, axis=0)
        X -= self.mean_

        # some constant terms
        nsqrt = sqrt(n_samples)
        llconst = n_features * log(2. * np.pi) + n_components
        var = np.var(X, axis=0)

        if self.noise_variance_init is None:
            psi = np.ones(n_features, dtype=X.dtype)
        else:
            if len(self.noise_variance_init) != n_features:
                raise ValueError("noise_variance_init dimension does not "
                                 "with number of features : %d != %d" %
                                 (len(self.noise_variance_init), n_features))
            psi = np.array(self.noise_variance_init)

        loglike = []
        old_ll = -np.inf
        SMALL = 1e-12

        # we'll modify svd outputs to return unexplained variance
        # to allow for unified computation of loglikelihood
        if self.svd_method == 'lapack':
            def my_svd(X):
                _, s, V = linalg.svd(X, full_matrices=False)
                return (s[:n_components], V[:n_components],
                        np.dot(s[n_components:].flat, s[n_components:].flat))
        elif self.svd_method == 'randomized':
            random_state = check_random_state(self.random_state)

            def my_svd(X):
                _, s, V = randomized_svd(X, n_components,
                                         random_state=random_state,
                                         n_iter=self.iterated_power)
                return s, V, np.dot(X.flat, X.flat) - np.dot(s, s)
        else:
            raise ValueError('SVD method %s is not supported. Please consider'
                             ' the documentation' % self.svd_method)

        for i in xrange(self.max_iter):
            # SMALL helps numerics
            sqrt_psi = np.sqrt(psi) + SMALL
            s, V, unexp_var = my_svd(X / (sqrt_psi * nsqrt))
            s **= 2
            # Use 'maximum' here to avoid sqrt problems.
            W = np.sqrt(np.maximum(s - 1., 0.))[:, np.newaxis] * V
            del V
            W *= sqrt_psi

            # loglikelihood
            ll = llconst + np.sum(np.log(s))
            ll += unexp_var + np.sum(np.log(psi))
            ll *= -n_samples / 2.
            loglike.append(ll)
            if (ll - old_ll) < self.tol:
                break
            old_ll = ll

            psi = np.maximum(var - np.sum(W ** 2, axis=0), SMALL)
        else:
            warnings.warn('FactorAnalysis did not converge.' +
                          ' You might want' +
                          ' to increase the number of iterations.',
                          ConvergenceWarning)

        self.components_ = W
        self.noise_variance_ = psi
        self.loglike_ = loglike
        return self

    def transform(self, X):
        """Apply dimensionality reduction to X using the model.

        Compute the expected mean of the latent variables.
        See Barber, 21.2.33 (or Bishop, 12.66).

        Parameters
        ----------
        X : array-like, shape (n_samples, n_features)
            Training data.

        Returns
        -------
        X_new : array-like, shape (n_samples, n_components)
            The latent variables of X.
        """
        X = array2d(X)
        Ih = np.eye(len(self.components_))

        X_transformed = X - self.mean_

        Wpsi = self.components_ / self.noise_variance_
        cov_z = linalg.inv(Ih + np.dot(Wpsi, self.components_.T))
        tmp = fast_dot(X_transformed, Wpsi.T)
        X_transformed = fast_dot(tmp, cov_z)

        return X_transformed

    def get_covariance(self):
        """Compute data covariance with the FactorAnalysis model.

        ``cov = components_.T * components_ + diag(noise_variance)``

        Returns
        -------
        cov : array, shape=(n_features, n_features)
            Estimated covariance of data.
        """
        cov = np.dot(self.components_.T, self.components_)
        cov.flat[::len(cov) + 1] += self.noise_variance_  # modify diag inplace
        return cov

    def score(self, X, y=None):
        """Compute score of X under FactorAnalysis model.

        Parameters
        ----------
        X: array of shape(n_samples, n_features)
            The data to test

        Returns
        -------
        ll: array of shape (n_samples),
            log-likelihood of each row of X under the current model
        """
        Xr = X - self.mean_
        cov = self.get_covariance()
        n_features = X.shape[1]
        log_like = np.zeros(X.shape[0])
        self.precision_ = linalg.inv(cov)
        log_like = -.5 * (Xr * (fast_dot(Xr, self.precision_))).sum(axis=1)
        log_like -= .5 * (fast_logdet(cov) + n_features * log(2. * np.pi))
        return log_like
