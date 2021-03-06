from termcolor import colored
import numpy as np

from pbdlib.functions import *
from pbdlib.model import *
from pbdlib.gmm import *

import math
from numpy.linalg import inv, pinv, norm, det
import sys
from collections import defaultdict
from scipy.stats import multivariate_normal


class HMM(GMM):
    def __init__(self, nb_states, nb_dim=2):
        GMM.__init__(self, nb_states, nb_dim)

        self._trans = None
        self._init_priors = None
        self.qs = {}
        self.hs = {}
        self._hs = {}

    @property
    def init_priors(self):
        if self._init_priors is None:
            print(colored(
                "HMM init priors not defined, initializing to uniform", 'red', 'on_white'))
            self._init_priors = np.ones(self.nb_states) / self.nb_states

        return self._init_priors

    @init_priors.setter
    def init_priors(self, value):
        self._init_priors = value

    @property
    def trans(self):
        if self._trans is None:
            print(colored(
                "HMM transition matrix not defined, initializing to uniform", 'red', 'on_white'))
            self._trans = np.ones(
                (self.nb_states, self.nb_states)) / self.nb_states
        return self._trans

    @trans.setter
    def trans(self, value):
        self._trans = value

    @property
    def Trans(self):
        return self.trans

    @Trans.setter
    def Trans(self, value):
        self.trans = value

    def make_finish_state(self, demos, dep_mask=None):
        self.has_finish_state = True
        self.nb_states += 1

        data = np.concatenate([d[-3:] for d in demos])

        mu = np.mean(data, axis=0)

        # Update covariances
        if data.shape[0] > 1:
            sigma = np.einsum('ai,aj->ij', data-mu, data-mu) / \
                (data.shape[0] - 1) + self.reg
        else:
            sigma = self.reg

        # if cov_type == 'diag':
        # 	self.sigma *= np.eye(self.nb_dim)

        if dep_mask is not None:
            sigma *= dep_mask

        self.mu = np.concatenate([self.mu, mu[None]], axis=0)
        self.sigma = np.concatenate([self.sigma, sigma[None]], axis=0)
        self.init_priors = np.concatenate(
            [self.init_priors, np.zeros(1)], axis=0)
        self.priors = np.concatenate([self.priors, np.zeros(1)], axis=0)
        pass

    def viterbi(self, demo, reg=False):
        """
        Compute most likely sequence of state given observations

        :param demo: 	[np.array([nb_timestep, nb_dim])]
        :return:
        """

        nb_data, dim = demo.shape if isinstance(
            demo, np.ndarray) else demo['x'].shape

        logB = np.zeros((self.nb_states, nb_data))
        logDELTA = np.zeros((self.nb_states, nb_data))
        PSI = np.zeros((self.nb_states, nb_data)).astype(int)

        _, logB = self.obs_likelihood(demo)

        # forward pass
        logDELTA[:, 0] = np.log(self.init_priors + realmin * reg) + logB[:, 0]

        for t in range(1, nb_data):
            for i in range(self.nb_states):
                # get index of maximum value : most probables
                PSI[i, t] = np.argmax(
                    logDELTA[:, t - 1] + np.log(self.Trans[:, i] + realmin * reg))
                logDELTA[i, t] = np.max(
                    logDELTA[:, t - 1] + np.log(self.Trans[:, i] + realmin * reg)) + logB[i, t]

        assert not np.any(np.isnan(logDELTA)), "Nan values"

        # backtracking
        q = [0 for i in range(nb_data)]
        q[-1] = np.argmax(logDELTA[:, -1])
        for t in range(nb_data - 2, -1, -1):
            q[t] = PSI[q[t + 1], t + 1]

        return q

    def split_kbins(self, demos):
        t_sep = []
        t_resp = []

        for demo in demos:
            t_sep += [list(map(int, np.round(
                np.linspace(0, demo.shape[0], self.nb_states + 1))))]

            resp = np.zeros((demo.shape[0], self.nb_states))

            # print t_sep
            for i in range(self.nb_states):
                resp[t_sep[-1][i]:t_sep[-1][i+1], i] = 1.0
            # print resp
            t_resp += [resp]

        return np.concatenate(t_resp)

    def obs_likelihood(self, demo=None, dep=None, marginal=None, sample_size=200, demo_idx=None):
        sample_size = demo.shape[0]
        # emission probabilities
        B = np.ones((self.nb_states, sample_size))

        if marginal != []:
            for i in range(self.nb_states):
                mu, sigma = (self.mu, self.sigma)

                if marginal is not None:
                    mu, sigma = self.get_marginal(marginal)

                if dep is None:
                    B[i, :] = multi_variate_normal(
                        demo, mu[i], sigma[i], log=True)

                else:  # block diagonal computation
                    B[i, :] = 0.
                    for d in dep:
                        if isinstance(d, list):
                            dGrid = np.ix_([i], d, d)
                            B[[i], :] += multi_variate_normal(demo[:, d], mu[i, d],
                                                              sigma[dGrid][0], log=True)
                        elif isinstance(d, slice):
                            B[[i], :] += multi_variate_normal(demo[:, d], mu[i, d],
                                                              sigma[i, d, d], log=True)

        return np.exp(B), B

    def online_forward_message(self, x, marginal=None, reset=False):
        """

        :param x:
        :param marginal: slice
        :param reset:
        :return:
        """
        if (not hasattr(self, '_marginal_tmp') or reset) and marginal is not None:
            self._marginal_tmp = self.marginal_model(marginal)

        if marginal is not None:
            B, _ = self._marginal_tmp.obs_likelihood(x[None])
        else:
            B, _ = self.obs_likelihood(x[None])

        if not hasattr(self, '_alpha_tmp') or reset:
            self._alpha_tmp = self.init_priors * B[:, 0]
        else:
            self._alpha_tmp = self._alpha_tmp.dot(self.Trans) * (B[:, 0])
        if np.count_nonzero(self._alpha_tmp) == 0:
            print(self._alpha_tmp)
        self._alpha_tmp /= np.sum(self._alpha_tmp, keepdims=True) 

        return self._alpha_tmp

    
    def marginal_model(self, dims):
        """
        Get a GMM of a slice of this GMM
        :param dims:
        :type dims: slice
        :return:
        """
        gmm = HMM(nb_dim=dims.stop-dims.start, nb_states=self.nb_states)
        gmm.priors = self.priors
        gmm.mu = self.mu[:, dims]
        gmm.sigma = self.sigma[:, dims, dims]

        return gmm

    def compute_messages(self, demo=None, dep=None, table=None, marginal=None, sample_size=200, demo_idx=None):
        """

        :param demo: 	[np.array([nb_timestep, nb_dim])]
        :param dep: 	[A x [B x [int]]] A list of list of dimensions
                Each list of dimensions indicates a dependence of variables in the covariance matrix
                E.g. [[0],[1],[2]] indicates a diagonal covariance matrix
                E.g. [[0, 1], [2]] indicates a full covariance matrix between [0, 1] and no
                covariance with dim [2]
        :param table: 	np.array([nb_states, nb_demos]) - composed of 0 and 1
                A mask that avoid some demos to be assigned to some states
        :param marginal: [slice(dim_start, dim_end)] or []
                If not None, compute messages with marginals probabilities
                If [] compute messages without observations, use size
                (can be used for time-series regression)
        :return:
        """
        if isinstance(demo, np.ndarray):
            sample_size = demo.shape[0]
        elif isinstance(demo, dict):
            sample_size = demo['x'].shape[0]

        B, _ = self.obs_likelihood(demo, dep, marginal, sample_size)
        # if table is not None:
        # 	B *= table[:, [n]]
        self._B = B

        # forward variable alpha (rescaled)
        alpha = np.zeros((self.nb_states, sample_size))
        alpha[:, 0] = self.init_priors * B[:, 0]

        c = np.zeros(sample_size)
        c[0] = 1.0 / np.sum(alpha[:, 0] + realmin)
        alpha[:, 0] = alpha[:, 0] * c[0]

        for t in range(1, sample_size):
            alpha[:, t] = alpha[:, t - 1].dot(self.Trans) * B[:, t]
            # Scaling to avoid underflow issues
            c[t] = 1.0 / np.sum(alpha[:, t] + realmin)
            alpha[:, t] = alpha[:, t] * c[t]

        # backward variable beta (rescaled)
        beta = np.zeros((self.nb_states, sample_size))
        beta[:, -1] = np.ones(self.nb_states) * c[-1]  # Rescaling
        for t in range(sample_size - 2, -1, -1):
            beta[:, t] = np.dot(self.Trans, beta[:, t + 1] * B[:, t + 1])
            beta[:, t] = np.minimum(beta[:, t] * c[t], realmax)

        # Smooth node marginals, gamma
        gamma = (alpha * beta) / np.tile(np.sum(alpha * beta, axis=0) + realmin,
                                         (self.nb_states, 1))

        # Smooth edge marginals. zeta (fast version, considers the scaling factor)
        zeta = np.zeros((self.nb_states, self.nb_states, sample_size - 1))

        for i in range(self.nb_states):
            for j in range(self.nb_states):
                zeta[i, j, :] = self.Trans[i, j] * alpha[i, 0:-1] * B[j, 1:] * beta[
                    j,
                    1:]

        return alpha, beta, gamma, zeta, c

    def init_params_random(self, data, left_to_right=False, self_trans=0.9):
        """

        :param data:
        :param left_to_right:  	if True, init with left to right. All observations models
                will be the same, and transition matrix will be set to l_t_r
        :type left_to_right: 	bool
        :param self_trans:		if left_to_right, self transition value to fill
        :type self_trans:		float
        :return:
        """
        mu = np.mean(data, axis=0)
        sigma = np.cov(data.T)

        if left_to_right:
            self.mu = np.array([mu for i in range(self.nb_states)])
        else:
            self.mu = np.array([np.random.multivariate_normal(mu, sigma)
                                for i in range(self.nb_states)])

        self.sigma = np.array(
            [sigma + self.reg for i in range(self.nb_states)])
        self.priors = np.ones(self.nb_states) / self.nb_states

        if left_to_right:
            self.Trans = np.zeros((self.nb_states, self.nb_states))
            for i in range(self.nb_states):
                if i < self.nb_states - 1:
                    self.Trans[i, i] = self_trans
                    self.Trans[i, i+1] = 1. - self_trans
                else:
                    self.Trans[i, i] = 1.

            self.init_priors = np.zeros(self.nb_states) / self.nb_states
        else:
            self.Trans = np.ones((self.nb_states, self.nb_states)) * \
                (1.-self_trans)/(self.nb_states-1)
            # remove diagonal
            self.Trans *= (1.-np.eye(self.nb_states))
            self.Trans += self_trans * np.eye(self.nb_states)
            self.init_priors = np.ones(self.nb_states) / self.nb_states

    def gmm_init(self, data, **kwargs):
        if isinstance(data, list):
            data = np.concatenate(data, axis=0)
        GMM.em(self, data, **kwargs)

        self.init_priors = np.ones(self.nb_states) / self.nb_states
        self.Trans = np.ones((self.nb_states, self.nb_states))/self.nb_states

    def init_loop(self, demos):
        self.Trans = 0.98 * np.eye(self.nb_states)
        for i in range(self.nb_states-1):
            self.Trans[i, i + 1] = 0.02

        self.Trans[-1, 0] = 0.02

        data = np.concatenate(demos, axis=0)
        _mu = np.mean(data, axis=0)
        _cov = np.cov(data.T)

        self.mu = np.array([_mu for i in range(self.nb_states)])
        self.sigma = np.array([_cov for i in range(self.nb_states)])

        self.init_priors = np.array(
            [1.] + [0. for i in range(self.nb_states-1)])

    def em(self, demos, dep=None, reg=1e-8, table=None, end_cov=False, cov_type='full', dep_mask=None,
           reg_finish=None, left_to_right=False, nb_max_steps=40, loop=False, obs_fixed=False, trans_reg=None):
        """

        :param demos:	[list of np.array([nb_timestep, nb_dim])]
                        or [lisf of dict({})]
        :param dep:		[A x [B x [int]]] A list of list of dimensions or slices
                Each list of dimensions indicates a dependence of variables in the covariance matrix
                !!! dimensions should not overlap eg : [[0], [0, 1]] should be [[0, 1]], [[0, 1], [1, 2]] should be [[0, 1, 2]]
                E.g. [[0],[1],[2]] indicates a diagonal covariance matrix
                E.g. [[0, 1], [2]] indicates a full covariance matrix between [0, 1] and no
                covariance with dim [2]
                E.g. [slice(0, 2), [2]] indicates a full covariance matrix between [0, 1] and no
                covariance with dim [2]
        :param reg:		[float] or list [nb_dim x float] for different regularization in different dimensions
                Regularization term used in M-step for covariance matrices
        :param table:		np.array([nb_states, nb_demos]) - composed of 0 and 1
                A mask that avoid some demos to be assigned to some states
        :param end_cov:	[bool]
                If True, compute covariance matrix without regularization after convergence
        :param cov_type: 	[string] in ['full', 'diag', 'spherical']
        :return:
        """

        if reg_finish is not None:
            end_cov = True

        nb_min_steps = 2  # min num iterations
        max_diff_ll = 1e-4  # max log-likelihood increase

        nb_samples = len(demos)
        data = np.concatenate(demos).T
        nb_data = data.shape[0]

        s = [{} for d in demos]
        # stored log-likelihood
        LL = np.zeros(nb_max_steps)

        if dep is not None:
            dep_mask = self.get_dep_mask(dep)

        self.reg = reg

        if self.mu is None or self.sigma is None:
            self.init_params_random(data.T, left_to_right=left_to_right)

        # create regularization matrix

        if left_to_right or loop:
            mask = np.eye(self.Trans.shape[0])
            for i in range(self.Trans.shape[0] - 1):
                mask[i, i + 1] = 1.
            if loop:
                mask[-1, 0] = 1.

        if dep_mask is not None:
            self.sigma *= dep_mask

        for it in range(nb_max_steps):

            for n, demo in enumerate(demos):
                s[n]['alpha'], s[n]['beta'], s[n]['gamma'], s[n]['zeta'], s[n]['c'] = HMM.compute_messages(
                    self, demo, dep, table)

            # concatenate intermediary vars
            gamma = np.hstack([s[i]['gamma'] for i in range(nb_samples)])
            zeta = np.dstack([s[i]['zeta'] for i in range(nb_samples)])
            gamma_init = np.hstack([s[i]['gamma'][:, 0:1]
                                    for i in range(nb_samples)])
            gamma_trk = np.hstack([s[i]['gamma'][:, 0:-1]
                                   for i in range(nb_samples)])

            gamma2 = gamma / (np.sum(gamma, axis=1, keepdims=True) + realmin)

            # M-step
            if not obs_fixed:
                for i in range(self.nb_states):
                    # Update centers
                    self.mu[i] = np.einsum('a,ia->i', gamma2[i], data)

                    # Update covariances
                    Data_tmp = data - self.mu[i][:, None]
                    self.sigma[i] = np.einsum('ij,jk->ik',
                                              np.einsum('ij,j->ij', Data_tmp,
                                                        gamma2[i, :]), Data_tmp.T)
                    # Regularization
                    self.sigma[i] = self.sigma[i] + self.reg

                    if cov_type == 'diag':
                        self.sigma[i] *= np.eye(self.sigma.shape[1])

                if dep_mask is not None:
                    self.sigma *= dep_mask

            # Update initial state probablility vector
            self.init_priors = np.mean(gamma_init, axis=1)

            # Update transition probabilities
            self.Trans = np.sum(zeta, axis=2) / \
                (np.sum(gamma_trk, axis=1) + realmin)

            if trans_reg is not None:
                self.Trans += trans_reg
                self.Trans /= np.sum(self.Trans, axis=1, keepdims=True)

            if left_to_right or loop:
                self.Trans *= mask
                self.Trans /= np.sum(self.Trans, axis=1, keepdims=True)

            # print self.Trans
            # Compute avarage log-likelihood using alpha scaling factors
            LL[it] = 0
            for n in range(nb_samples):
                LL[it] -= sum(np.log(s[n]['c']))
            LL[it] = LL[it] / nb_samples

            self._gammas = [s_['gamma'] for s_ in s]

            # Check for convergence
            if it > nb_min_steps and LL[it] - LL[it - 1] < max_diff_ll:
                print("EM converges")
                if end_cov:
                    for i in range(self.nb_states):
                        # recompute covariances without regularization
                        Data_tmp = data - self.mu[i][:, None]
                        self.sigma[i] = np.einsum('ij,jk->ik',
                                                  np.einsum('ij,j->ij', Data_tmp,
                                                            gamma2[i, :]), Data_tmp.T)
                        if reg_finish is not None:
                            self.reg = reg_finish
                            self.sigma += self.reg[None]

                    if cov_type == 'diag':
                        self.sigma[i] *= np.eye(self.sigma.shape[1])

                # print "EM converged after " + str(it) + " iterations"
                # print LL[it]

                if dep_mask is not None:
                    self.sigma *= dep_mask

                return True

        print("EM did not converge")
        return False

    def score(self, demos):
        """

        :param demos:	[list of np.array([nb_timestep, nb_dim])]
        :return:
        """
        ll = []
        for n, demo in enumerate(demos):
            _, _, _, _, c = HMM.compute_messages(self, demo)
            ll += [np.sum(np.log(c))]

        return ll

    def condition(self, data_in, dim_in, dim_out, h=None, gmm=False, return_gmm=False):
        if gmm:
            return super(HMM, self).condition(data_in, dim_in, dim_out, return_gmm=return_gmm)
        else:
            a, _, _, _, _ = self.compute_messages(data_in, marginal=dim_in)

            return super(HMM, self).condition(data_in, dim_in, dim_out, h=a)

    """
	To ensure compatibility
	"""
    @property
    def Trans(self):
        return self.trans

    @Trans.setter
    def Trans(self, value):
        self.trans = value


    def predict_qdot(self, q, t):
        q_dim = len(q)
        q_dot = np.zeros((q_dim))
        reset = False
        for i in range(self.nb_states):
            a = q - self.mu[i][0:q_dim]
            b = inv(self.sigma[i][0:q_dim , 0:q_dim]) @ a
            c = self.sigma[i][q_dim:, 0:q_dim] @ b
            d = self.mu[i][q_dim:] + c
            #h = self.h(i, q, t)
            if t == 0:
                reset = True
            h = self.online_forward_message(q, marginal=slice(0, 7), reset=reset)
            self.hs[i, t] = h[i]
            q_dot += h[i] * d
        return q_dot


    def predict_q(self, q_dot, q, t):
        q_dim = len(q_dot)
        q_new = np.zeros((q_dim))
        for i in range(self.nb_states):
            a = q_dot - self.mu[i][q_dim:]
            b = inv(self.sigma[i][q_dim: , q_dim:]) @ a
            c = self.sigma[i][0:q_dim, q_dim:] @ b
            d = self.mu[i][:q_dim] + c
            h = self.hs[i, t]
            # maybe q_dot instead of q in h
            q_new += h * d
        return q_new

    def h(self, i, q, t):
        if (i, t) in self._hs.keys():
            return self._hs[i, t]
        return self.h_right(i, q, t)
        
    def _normal_q(self, q, i):
        q_dim = len(q)
        #a = multivariate_normal.pdf(q,mean=self.mu[i][:q_dim], cov=self.sigma[i][:q_dim, :q_dim])
        a = multi_variate_normal(np.array([q]), self.mu[i][0:q_dim], sigma=self.sigma[i][0:q_dim, 0:q_dim], log=True)
        a = np.exp(a)
        #a = q - self.mu[i][:q_dim]
        #sigma = self.sigma[i][:q_dim, :q_dim]
        #änorm = np.sqrt((2*np.pi)**q_dim) * np.abs(np.linalg.det(sigma))
        #prob = a.T @ sigma @ a
        #a = (- 0.5 * prob)/ norm 
        return a + np.finfo(float).tiny
        
    def _history(self, q, t):
        q_dim=len(q)
        if t == 0:
            s = 0
            for i in range(self.nb_states):
                res = float(self.priors[i] * self._normal_q(q, i))
                s += res
                self._hs[i, t] = res
            for j in range(self.nb_states):
                self._hs[j, t] = self._hs[j, t] / s
        else:
            for i in range(self.nb_states):
                res = 0
                for j in range(self.nb_states):
                    res += self._hs[j, t-1] * self.Trans[j][i]
                res *= float(self._normal_q(q, i))
                self._hs[i, t] = res
            s = 0
            for k in range(self.nb_states):
                res = 0
                for j in range(self.nb_states):
                    res += self._hs[j, t-1] * self.Trans[j][k]
                res *= float(self._normal_q(q, k))
                s += res
            for i in range(self.nb_states):
                self._hs[i, t] /= s
    
    def h_right(self, i, q, t):
        self.qs[t] = q
        if t == 0:
            res = self.priors[i] * self._normal_q(q, i)
            s = 0
            for k in range(self.nb_states):
                s += self.priors[k] * self._normal_q(q, i)
            res /= s
        else:
            num = 0
            for j in range(self.nb_states):
                num += self._hs[j, t-1] * self.Trans[i][j]
            num *= self._normal_q(q, i)
            denom = 0
            for k in range(self.nb_states):
                tmp = 0
                for j in range(self.nb_states):
                    tmp += self._hs[j, t-1] * self.Trans[k][j]
                tmp += self._normal_q(q, k)
                denom += tmp
            res = num / denom
        self._hs[i, t] = res
        return res 