# emacs: -*- mode: python; py-indent-offset: 4; indent-tabs-mode: nil -*-
# vi: set ft=python sts=4 ts=4 sw=4 et:
"""The spm module provides basic functions for interfacing with matlab
and spm to access spm tools.

"""

__docformat__ = 'restructuredtext'

# Standard library imports
import logging
import os
from glob import glob

# Third-party imports
import numpy as np
import scipy.io as sio

# Local imports
from nipype.interfaces.base import Bunch, traits, \
    TraitedSpec, File, Directory, OutputMultiPath, InputMultiPath
from nipype.interfaces.spm.base import SPMCommand, SPMCommandInputSpec
from nipype.utils.misc import isdefined
from nipype.utils.filemanip import (filename_to_list, list_to_filename,
                                    loadflat)

logger = logging.getLogger('spmlogger')

class Level1DesignInputSpec(SPMCommandInputSpec):
    spm_mat_dir = Directory(exists=True, field='dir', desc='directory to store SPM.mat file (opt)')
    timing_units = traits.Enum('secs', 'scans', field='timing.units', desc='units for specification of onsets')
    interscan_interval = traits.Float(field='timing.RT', desc='Interscan interval in secs')
    microtime_resolution = traits.Int(field='timing.fmri_t',
                        desc='Number of time-bins per scan in secs (opt)')
    microtime_onset = traits.Float(field='timing.fmri_t0',
                        desc='The onset/time-bin in seconds for alignment (opt)')
    session_info = File(exists=True, field='sess', desc='Session specific information file')
    factor_info = traits.List(traits.Dict(traits.Enum('name','levels')),
                              field='fact', desc='Factor specific information file (opt)')
    bases = traits.Dict(traits.Enum('hrf', 'fourier', 'fourier_han',
                'gamma', 'fir'), field='bases', desc="""
            dict {'name':{'basesparam1':val,...}}
            name : string
                Name of basis function (hrf, fourier, fourier_han,
                gamma, fir)
                
                hrf :
                    derivs : 2-element list
                        Model  HRF  Derivatives. No derivatives: [0,0],
                        Time derivatives : [1,0], Time and Dispersion
                        derivatives: [1,1]
                fourier, fourier_han, gamma, fir:
                    length : int
                        Post-stimulus window length (in seconds)
                    order : int
                        Number of basis functions
""")
    volterra_expansion_order = traits.Enum(1, 2 , field='volt',
                     desc='Model interactions - yes:1, no:2 (opt)')
    global_intensity_normalization = traits.Enum('none', 'scaling', field='global',
                      desc='Global intensity normalization - scaling or none (opt)')
    mask_image = File(exists=True, field='mask', copyfile=False,
                      desc='Image  for  explicitly  masking the analysis (opt)')
    mask_threshold = traits.Either(traits.Enum('-Inf'), traits.Float(),
                      desc="Thresholding for the mask (opt, '-Inf')", default='-Inf', usedefault=True)
    model_serial_correlations = traits.Enum('AR(1)', 'none', field='cvi',
                      desc='Model serial correlations AR(1) or none (opt)')

class Level1DesignOutputSpec(TraitedSpec):
    spm_mat_file = File(exists=True, desc='SPM mat file')


class Level1Design(SPMCommand):
    """Generate an SPM design matrix
    
    http://www.fil.ion.ucl.ac.uk/spm/doc/manual.pdf#page=61

    Examples
    --------
    >>> level1design = Level1Design()
    >>> level1design.inputs.timing_units = 'secs'
    >>> level1design.inputs.interscan_interval = 2.5
    >>> level1design.inputs.bases = {'hrf':{'derivs': [0,0]}}
    >>> level1design.inputs.session_info = '../../emptydata/session_info.npz'
    >>> level1design.run() # doctest: +SKIP
    """

    input_spec = Level1DesignInputSpec
    output_spec = Level1DesignOutputSpec

    _jobtype = 'stats'
    _jobname = 'fmri_spec'

    def _format_arg(self, opt, spec, val):
        """Convert input to appropriate format for spm
        """
        if opt in ['spm_mat_dir', 'mask_image']:
            return np.array([str(val)], dtype=object)
        if opt in ['session_info']: #, 'factor_info']:
            data = loadflat(val, opt)
            if isinstance(data[opt], dict):
                return [data[opt]]
            else:
                return data[opt]
        return val

    def _parse_inputs(self):
        """validate spm realign options if set to None ignore
        """
        einputs = super(Level1Design, self)._parse_inputs(skip=('mask_threshold'))
        if not isdefined(self.inputs.spm_mat_dir):
            einputs[0]['dir'] = np.array([str(os.getcwd())], dtype=object)
        return einputs

    def _make_matlab_command(self, content):
        """validates spm options and generates job structure
        if mfile is True uses matlab .m file
        else generates a job structure and saves in .mat
        """
        if isdefined(self.inputs.mask_image):
            # SPM doesn't handle explicit masking properly, especially
            # when you want to use the entire mask image
            postscript = "load SPM;\n"
            postscript += "SPM.xM.VM = spm_vol('%s');\n" % list_to_filename(self.inputs.mask_image)
            postscript += "SPM.xM.I = 0;\n"
            postscript += "SPM.xM.T = [];\n"
            postscript += "SPM.xM.TH = ones(size(SPM.xM.TH))*(%s);\n" % self.inputs.mask_threshold
            postscript += "SPM.xM.xs = struct('Masking', 'explicit masking only');\n"
            postscript += "save SPM SPM;\n"
        else:
            postscript = None
        return super(Level1Design, self)._make_matlab_command(content, postscript=postscript)

    def _list_outputs(self):
        outputs = self._outputs().get()
        spm = os.path.join(os.getcwd(), 'SPM.mat')
        outputs['spm_mat_file'] = spm
        return outputs


class EstimateModelInputSpec(SPMCommandInputSpec):
    spm_mat_file = File(exists=True, field='spmmat', desc='absolute path to SPM.mat', copyfile=True)
    estimation_method = traits.Dict(traits.Enum('Classical', 'Bayesian2', 'Bayesian'), field='method',
                                     desc='Classical, Bayesian2, Bayesian (dict)')
    flags = traits.Str(desc='optional arguments (opt)')

class EstimateModelOutputSpec(TraitedSpec):
    mask_image = File(exists=True, desc='binary mask to constrain estimation')
    beta_images = OutputMultiPath(File(exists=True), desc='design parameter estimates')
    residual_image = File(exists=True, desc='Mean-squared image of the residuals')
    RPVimage = File(exists=True, desc='Resels per voxel image')
    spm_mat_file = File(exist=True, desc='Updated SPM mat file')

class EstimateModel(SPMCommand):
    """Use spm_spm to estimate the parameters of a model
    
    http://www.fil.ion.ucl.ac.uk/spm/doc/manual.pdf#page=71
    
    Examples
    --------
    >>> est = EstimateModel()
    >>> est.inputs.spm_mat_file = '../../emptydata/SPM.mat'
    >>> est.run() # doctest: +SKIP
    """
    input_spec = EstimateModelInputSpec
    output_spec = EstimateModelOutputSpec
    _jobtype = 'stats'
    _jobname = 'fmri_est'

    def _format_arg(self, opt, spec, val):
        """Convert input to appropriate format for spm
        """
        if opt == 'spm_mat_file':
            return np.array([str(val)], dtype=object)
        if opt == 'estimation_method':
            if isinstance(val, str):
                return {'%s' % val:1}
            else:
                return val
        return val

    def _parse_inputs(self):
        """validate spm realign options if set to None ignore
        """
        einputs = super(EstimateModel, self)._parse_inputs(skip=('flags'))
        if isdefined(self.inputs.flags):
            einputs[0].update(self.inputs.flags)
        return einputs

    def _list_outputs(self):
        outputs = self._outputs().get()
        pth, _ = os.path.split(self.inputs.spm_mat_file)
        mask = os.path.join(pth, 'mask.img')
        outputs['mask_image'] = mask
        spm = sio.loadmat(self.inputs.spm_mat_file)
        betas = []
        for vbeta in spm['SPM'][0, 0].Vbeta[0]:
            betas.append(str(os.path.join(pth, vbeta.fname[0])))
        if betas:
            outputs['beta_images'] = betas
        resms = os.path.join(pth, 'ResMS.img')
        outputs['residual_image'] = resms
        rpv = os.path.join(pth, 'RPV.img')
        outputs['RPVimage'] = rpv
        spm = os.path.join(pth, 'SPM.mat')
        outputs['spm_mat_file'] = spm
        return outputs

class EstimateContrastInputSpec(SPMCommandInputSpec):
    spm_mat_file = File(exists=True, field='spmmat', desc='Absolute path to SPM.mat', copyfile=True)
    contrasts = traits.List(
        traits.Either(traits.Tuple(traits.Str,
                                   traits.Enum('T'),
                                   traits.List(traits.Str),
                                   traits.List(traits.Float)),
                      traits.Tuple(traits.Str,
                                   traits.Enum('T'),
                                   traits.List(traits.Str),
                                   traits.List(traits.Float),
                                   traits.List(traits.Float)),
                      traits.Tuple(traits.Str,
                                   traits.Enum('F'),
                                   traits.List(traits.Either(traits.Tuple(traits.Str,
                                                                          traits.Enum('T'),
                                                                          traits.List(traits.Str),
                                                                          traits.List(traits.Float)),
                                                             traits.Tuple(traits.Str,
                                                                          traits.Enum('T'),
                                                                          traits.List(traits.Str),
                                                                          traits.List(traits.Float),
                                                                          traits.List(traits.Float)))))),
        desc="""List of contrasts with each contrast being a list of the form:
            [('name', 'stat', [condition list], [weight list], [session list])]. if
            session list is None or not provided, all sessions are used. For F
            contrasts, the condition list should contain previously defined
            T-contrasts.""")
    beta_images = InputMultiPath(File(exists=True), desc='Parameter estimates of the design matrix', copyfile=False)
    residual_image = File(exists=True, desc='Mean-squared image of the residuals', copyfile=False)
    ignore_derivs = traits.Bool(True, desc='ignore derivatives for estimation', usedefault=True)

class EstimateContrastOutputSpec(TraitedSpec):
    con_images = OutputMultiPath(File(exists=True), desc='contrast images from a t-contrast')
    spmT_images = OutputMultiPath(File(exists=True), desc='stat images from a t-contrast')
    ess_images = OutputMultiPath(File(exists=True), desc='contrast images from an F-contrast')
    spmF_images = OutputMultiPath(File(exists=True), desc='stat images from an F-contrast')
    spm_mat_file = File(exist=True, desc='Updated SPM mat file')

class EstimateContrast(SPMCommand):
    """use spm_contrasts to estimate contrasts of interest

    Examples
    --------
    >>> import nipype.interfaces.spm as spm
    >>> est = spm.EstimateContrast()
    >>> est.inputs.spm_mat_file = '../../emptydata/SPM.mat'
    >>> cont1 = ('Task>Baseline','T', ['Task-Odd','Task-Even'],[0.5,0.5])
    >>> cont2 = ('Task-Odd>Task-Even','T', ['Task-Odd','Task-Even'],[1,-1])
    >>> contrasts = [cont1,cont2]
    >>> est.inputs.contrasts = contrasts
    >>> est.run() # doctest: +SKIP
    
    """

    input_spec = EstimateContrastInputSpec
    output_spec = EstimateContrastOutputSpec
    _jobtype = 'stats'
    _jobname = 'con'

    def _make_matlab_command(self, _):
        """validates spm options and generates job structure
        """
        contrasts = []
        cname = []
        for i, cont in enumerate(self.inputs.contrasts):
            cname.insert(i, cont[0])
            contrasts.insert(i, Bunch(name=cont[0],
                                     stat=cont[1],
                                     conditions=cont[2],
                                     weights=None,
                                     sessions=None))
            if len(cont) >= 4:
                contrasts[i].weights = cont[3]
            if len(cont) >= 5:
                contrasts[i].sessions = cont[4]
        script = "% generated by nipype.interfaces.spm\n"
        script += "spm_defaults;\n"
        script += "jobs{1}.stats{1}.con.spmmat  = {'%s'};\n" % self.inputs.spm_mat_file
        script += "load(jobs{1}.stats{1}.con.spmmat{:});\n"
        script += "SPM.swd = '%s';\n" % os.getcwd()
        script += "save(jobs{1}.stats{1}.con.spmmat{:},'SPM');\n"
        script += "names = SPM.xX.name;\n"
        # get names for columns
        if self.inputs.ignore_derivs:
            script += "pat = 'Sn\([0-9*]\) (.*)\*bf\(1\)|Sn\([0-9*]\) .*\*bf\([2-9]\)|Sn\([0-9*]\) (.*)';\n"
        else:
            script += "pat = 'Sn\([0-9*]\) (.*)';\n"
        script += "t = regexp(names,pat,'tokens');\n"
        # get sessidx for columns
        script += "pat1 = 'Sn\(([0-9].*)\)\s.*';\n"
        script += "t1 = regexp(names,pat1,'tokens');\n"
        script += "for i0=1:numel(t),condnames{i0}='';condsess(i0)=0;if ~isempty(t{i0}{1}),condnames{i0} = t{i0}{1}{1};condsess(i0)=str2num(t1{i0}{1}{1});end;end;\n"
        # BUILD CONTRAST SESSION STRUCTURE
        for i, contrast in enumerate(contrasts):
            if contrast.stat == 'T':
                script += "consess{%d}.tcon.name   = '%s';\n" % (i + 1, contrast.name)
                script += "consess{%d}.tcon.convec = zeros(1,numel(names));\n" % (i + 1)
                for c0, cond in enumerate(contrast.conditions):
                    script += "idx = strmatch('%s',condnames,'exact');\n" % (cond)
                    if contrast.sessions:
                        for sno, sw in enumerate(contrast.sessions):
                            script += "sidx = find(condsess(idx)==%d);\n" % (sno + 1)
                            script += "consess{%d}.tcon.convec(idx(sidx)) = %f;\n" % (i + 1, sw * contrast.weights[c0])
                    else:
                        script += "consess{%d}.tcon.convec(idx) = %f;\n" % (i + 1, contrast.weights[c0])
        for i, contrast in enumerate(contrasts):
            if contrast.stat == 'F':
                script += "consess{%d}.fcon.name   =  '%s';\n" % (i + 1, contrast.name)
                for cl0, fcont in enumerate(contrast.conditions):
                    try:
                        tidx = cname.index(fcont[0])
                    except:
                        Exception("Contrast Estimate: could not get index of" \
                                  " T contrast. probably not defined prior " \
                                      "to the F contrasts")
                    script += "consess{%d}.fcon.convec{%d} = consess{%d}.tcon.convec;\n" % (i + 1, cl0 + 1, tidx + 1)
        script += "jobs{1}.stats{1}.con.consess = consess;\n"
        script += "if strcmp(spm('ver'),'SPM8'), spm_jobman('initcfg');jobs=spm_jobman('spm5tospm8',{jobs});end\n"
        script += "spm_jobman('run',jobs);"
        return script

    def _list_outputs(self):
        outputs = self._outputs().get()
        pth, _ = os.path.split(self.inputs.spm_mat_file)
        spm = sio.loadmat(self.inputs.spm_mat_file)
        con_images = []
        spmT_images = []
        for con in spm['SPM'][0, 0].xCon[0]:
            con_images.append(str(os.path.join(pth, con.Vcon[0, 0].fname[0])))
            spmT_images.append(str(os.path.join(pth, con.Vspm[0, 0].fname[0])))
        if con_images:
            outputs['con_images'] = con_images
            outputs['spmT_images'] = spmT_images
        ess = glob(os.path.join(pth, 'ess*.img'))
        if len(ess) > 0:
            outputs['ess_images'] = sorted(ess)
        spmf = glob(os.path.join(pth, 'spmF*.img'))
        if len(spmf) > 0:
            outputs['spmF_images'] = sorted(spmf)
        outputs['spm_mat_file'] = self.inputs.spm_mat_file
        return outputs

class OneSampleTTestInputSpec(SPMCommandInputSpec):
    con_images = InputMultiPath(File(exist=True, desc='List of contrast images'), mandatory=True)

class OneSampleTTestOutputSpec(TraitedSpec):
    con_images = OutputMultiPath(File(exist=True, desc='contrast images from a t-contrast'))
    spmT_images = OutputMultiPath(File(exist=True, desc='stat images from a t-contrast'))

class OneSampleTTest(SPMCommand):
    """use spm to perform a one-sample ttest on a set of images

    Examples
    --------
    >>> import nipype.interfaces.spm as spm
    >>> ttest = spm.OneSampleTTest()
    >>> ttest.inputs.con_images = ['../../emptydata/cont1.nii', '../../emptydata/cont2.nii']
    >>> ttest.run() # doctest: +SKIP
    """
    input_spec = OneSampleTTestInputSpec
    output_spec = OneSampleTTestOutputSpec
    _jobtype = 'stats'

    def _make_matlab_command(self, _):
        """validates spm options and generates job structure
        """
        cwd = os.getcwd()
        script = "% generated by nipype.interfaces.spm\n"
        script += "spm_defaults;\n\n"
        script += "% Setup Design;\n"
        script += "jobs{1}.stats{1}.factorial_design.dir  = {'%s'};\n" % cwd
        script += "jobs{1}.stats{1}.factorial_design.des.t1.scans = {};\n"
        for f in filename_to_list(self.inputs.con_images):
            script += "jobs{1}.stats{1}.factorial_design.des.t1.scans{end+1} = '%s';\n" % f
        (_, fname) = os.path.split(f)
        (conname, _) = os.path.splitext(fname)
        script += "\n% Estimate Model;\n"
        script += "jobs{2}.stats{1}.fmri_est(1).spmmat = {'%s'};\n\n" % os.path.join(cwd, 'SPM.mat')
        script += "% Estimate Contrast;\n"
        script += "jobs{3}.stats{1}.con.spmmat = {'%s'};\n" % os.path.join(cwd, 'SPM.mat')
        script += "jobs{3}.stats{1}.con.consess{1}.tcon.name = '%s';\n" % conname
        script += "jobs{3}.stats{1}.con.consess{1}.tcon.convec = [1];\n"
        script += "if strcmp(spm('ver'),'SPM8'), spm_jobman('initcfg');jobs=spm_jobman('spm5tospm8',{jobs});end\n"
        script += "spm_jobman('run',jobs);\n"
        return script

    def _list_outputs(self):
        outputs = self._outputs().get()
        pth = os.getcwd()
        con = glob(os.path.join(pth, 'con*.img'))
        if len(con) > 0:
            outputs['con_images'] = sorted(con)
        spmt = glob(os.path.join(pth, 'spmT*.img'))
        if len(spmt) > 0:
            outputs['spmT_images'] = sorted(spmt)
        return outputs

class TwoSampleTTestInputSpec(SPMCommandInputSpec):
    images_group1 = traits.List(File(exists=True), desc='con images from group 1', mandatory=True)
    images_group2 = traits.List(File(exists=True), desc='con images from group 2', mandatory=True)
    dependent = traits.Bool(desc='Are the measurements independent between levels')
    unequal_variance = traits.Bool(desc='Are the variances equal or unequal between groups')

class TwoSampleTTestOutputSpec(TraitedSpec):
    con_images = traits.List(File(exist=True), desc='contrast images from a t-contrast')
    spmT_images = traits.List(File(exist=True), desc='stat images from a t-contrast')

class TwoSampleTTest(SPMCommand):
    """Perform a two-sample ttest using two groups of images

    4 contrasts are automatically created corresponding to:

    * group 1
    * group 2
    * group 1 - group 2
    * group 2 - group 1
    
    Examples
    --------
    >>> import nipype.interfaces.spm as spm
    >>> ttest = spm.TwoSampleTTest()
    >>> ttest.inputs.images_group1 = ['../../emptydata/cont1.nii', '../../emptydata/cont2.nii']
    >>> ttest.inputs.images_group2 = ['../../emptydata/cont1a.nii', '../../emptydata/cont2a.nii']
    >>> ttest.dependent = False
    >>> ttest.unequal_variance = True
    >>> ttest.run() # doctest: +SKIP
    """

    _jobtype = 'stats'
    input_spec = TwoSampleTTestInputSpec
    output_spec = TwoSampleTTestOutputSpec

    def _make_matlab_command(self, _):
        """validates spm options and generates job structure
        if mfile is True uses matlab .m file
        else generates a job structure and saves in .mat
        """
        cwd = os.getcwd()
        script = "% generated by nipype.interfaces.spm\n"
        script += "spm_defaults;\n\n"
        script += "% Setup Design;\n"
        script += "jobs{1}.stats{1}.factorial_design.dir  = {'%s'};\n" % cwd
        script += "jobs{1}.stats{1}.factorial_design.des.t2.scans1 = {};\n"
        for f in filename_to_list(self.inputs.images_group1):
            script += "jobs{1}.stats{1}.factorial_design.des.t2.scans1{end+1,1} = '%s';\n" % f
        script += "jobs{1}.stats{1}.factorial_design.des.t2.scans2 = {};\n"
        for f in filename_to_list(self.inputs.images_group2):
            script += "jobs{1}.stats{1}.factorial_design.des.t2.scans2{end+1,1} = '%s';\n" % f
        if isdefined(self.inputs.dependent):
            script += "jobs{1}.stats{1}.factorial_design.des.t2.dept = %d;\n" % self.inputs.dependent
        if isdefined(self.inputs.unequal_variance):
            script += "jobs{1}.stats{1}.factorial_design.des.t2.variance = %d;\n" % self.inputs.unequal_variance
        script += "\n% Estimate Model;\n"
        script += "jobs{2}.stats{1}.fmri_est(1).spmmat = {'%s'};\n\n" % os.path.join(cwd, 'SPM.mat')
        script += "% Estimate Contrast;\n"
        script += "jobs{3}.stats{1}.con.spmmat = {'%s'};\n" % os.path.join(cwd, 'SPM.mat')
        script += "jobs{3}.stats{1}.con.consess{1}.tcon.name = 'Group 1';\n"
        script += "jobs{3}.stats{1}.con.consess{1}.tcon.convec = [1 0];\n"
        script += "jobs{3}.stats{1}.con.consess{2}.tcon.name = 'Group 2';\n"
        script += "jobs{3}.stats{1}.con.consess{2}.tcon.convec = [0 1];\n"
        script += "jobs{3}.stats{1}.con.consess{3}.tcon.name = 'Group 1 - Group 2';\n"
        script += "jobs{3}.stats{1}.con.consess{3}.tcon.convec = [1 -1];\n"
        script += "jobs{3}.stats{1}.con.consess{4}.tcon.name = 'Group 2 - Group 1';\n"
        script += "jobs{3}.stats{1}.con.consess{4}.tcon.convec = [-1 1];\n"
        script += "if strcmp(spm('ver'),'SPM8'), spm_jobman('initcfg');jobs=spm_jobman('spm5tospm8',{jobs});end\n"
        script += "spm_jobman('run',jobs);\n"

        return script

    def _list_outputs(self):
        outputs = self._outputs().get()
        pth = os.getcwd()
        outputs['con_images'] = [os.path.join(pth, 'con%04d.img' % i) for i in range(1, 5)]
        outputs['spmT_images'] = [os.path.join(pth, 'spmT%04d.img' % i) for i in range(1, 5)]
        return outputs

class MultipleRegressionInputSpec(SPMCommandInputSpec):
    images = traits.List(File(exists=True), desc='con images from group 1',
                         mandatory=True)
    covariates = traits.Dict(key_trait=traits.Enum('vectors', 'names', 'centering'),
                             desc='dict of covariates {vectors, names, centering}',
                             mandatory=True)
    contrasts = contrasts = traits.List(
        traits.Either(traits.Tuple(traits.Str,
                                   traits.Enum('T'),
                                   traits.List(traits.Str),
                                   traits.List(traits.Float)),
                      traits.Tuple(traits.Str,
                                   traits.Enum('T'),
                                   traits.List(traits.Str),
                                   traits.List(traits.Float),
                                   traits.List(traits.Float)),
                      traits.Tuple(traits.Str,
                                   traits.Enum('F'),
                                   traits.List(traits.Either(traits.Tuple(traits.Str,
                                                                          traits.Enum('T'),
                                                                          traits.List(traits.Str),
                                                                          traits.List(traits.Float)),
                                                             traits.Tuple(traits.Str,
                                                                          traits.Enum('T'),
                                                                          traits.List(traits.Str),
                                                                          traits.List(traits.Float),
                                                                          traits.List(traits.Float)))))),
                            desc="""List of contrasts with each contrast being a list of the form:
            [('name', 'stat', [condition list], [weight list], [session list])]. if
            session list is None or not provided, all sessions are used. For F
            contrasts, the condition list should contain previously defined
            T-contrasts.""", mandatory=True)
    include_intercept = traits.Bool(True, desc='Include intercept in model', usedefault=True)

class MultipleRegressionOutputSpec(TraitedSpec):
    con_images = OutputMultiPath(File(exists=True), desc='contrast images from a t-contrast')
    spmT_images = OutputMultiPath(File(exists=True), desc='stat images from a t-contrast')
    ess_images = OutputMultiPath(File(exists=True), desc='contrast images from an F-contrast')
    spmF_images = OutputMultiPath(File(exists=True), desc='stat images from an F-contrast')

class MultipleRegression(SPMCommand):
    """Perform a two-sample ttest using two groups of images

    Examples
    --------

    >>> from nipype.interfaces.spm import MultipleRegression
    >>> mreg= MultipleRegression()
    >>> covariates = dict(names=['reg1', 'reg2'], centering=[1,1])
    >>> covariates['vectors'] = [[12,24],[0.6 -0.9]]
    >>> mreg.inputs.covariates = covariates
    >>> mreg.inputs.images = ['../../emptydata/cont1.nii', '../../emptydata/cont1.nii']
    >>> mreg.inputs.contrasts = [['reg2 > reg1', 'T', ['reg1','reg2'], [-1,1]]]
    >>> mreg.run() # doctest: +SKIP
    """

    _jobtype = 'stats'
    input_spec = MultipleRegressionInputSpec
    output_spec = MultipleRegressionOutputSpec

    def _make_matlab_command(self, _):
        """validates spm options and generates job structure
        if mfile is True uses matlab .m file
        else generates a job structure and saves in .mat
        """
        cwd = os.getcwd()
        script = "% generated by nipype.interfaces.spm\n"
        script += "spm_defaults;\n\n"
        script += "% Setup Design;\n"
        script += "jobs{1}.stats{1}.factorial_design.dir  = {'%s'};\n" % cwd
        script += "jobs{1}.stats{1}.factorial_design.des.mreg.scans = {};\n"
        for f in filename_to_list(self.inputs.images):
            script += "jobs{1}.stats{1}.factorial_design.des.mreg.scans{end+1,1} = '%s';\n" % f
        script += "jobs{1}.stats{1}.factorial_design.des.mreg.mcov = [];\n"
        for i, name in enumerate(self.inputs.covariates['names']):
            script += "names{%d} = '%s';\n" % (i + 1, name)
            script += "jobs{1}.stats{1}.factorial_design.des.mreg.mcov(end+1,1).cname = '%s';\n" % name
            centering = self.inputs.covariates['centering'][i]
            script += "jobs{1}.stats{1}.factorial_design.des.mreg.mcov(end,1).iCC = %d;\n" % centering
            for j, v in enumerate(self.inputs.covariates['vectors'][i]):
                script += "jobs{1}.stats{1}.factorial_design.des.mreg.mcov(end,1).c(%d,1)" \
                    " = %f;\n" % (j + 1, v)
        if self.inputs.include_intercept:
            script += "names{end+1} = 'mean';\n"
        script += "jobs{1}.stats{1}.factorial_design.des.mreg.incint = %d;\n" % self.inputs.include_intercept
        script += "\n% Estimate Model;\n"
        script += "jobs{2}.stats{1}.fmri_est(1).spmmat = {'%s'};\n\n" % os.path.join(cwd, 'SPM.mat')
        script += "% Estimate Contrast;\n"
        script += "jobs{3}.stats{1}.con.spmmat = {'%s'};\n" % os.path.join(cwd, 'SPM.mat')
        contrasts = []
        cname = []
        for i, cont in enumerate(self.inputs.contrasts):
            cname.insert(i, cont[0])
            contrasts.insert(i, Bunch(name=cont[0],
                                     stat=cont[1],
                                     conditions=cont[2],
                                     weights=None))
            if len(cont) >= 4:
                contrasts[i].weights = cont[3]
        for i, contrast in enumerate(contrasts):
            if contrast.stat == 'T':
                script += "consess{%d}.tcon.name   = '%s';\n" % (i + 1, contrast.name)
                script += "consess{%d}.tcon.convec = zeros(1,numel(names));\n" % (i + 1)
                for c0, cond in enumerate(contrast.conditions):
                    script += "idx = strmatch('%s',names,'exact');\n" % (cond)
                    script += "consess{%d}.tcon.convec(idx) = %f;\n" % (i + 1, contrast.weights[c0])
            elif contrast.stat == 'F':
                script += "consess{%d}.fcon.name   =  '%s';\n" % (i + 1, contrast.name)
                for cl0, fcont in enumerate(contrast.conditions):
                    tidx = cname.index(fcont[0])
                    script += "consess{%d}.fcon.convec{%d} = consess{%d}.tcon.convec;\n" % (i + 1, cl0 + 1, tidx + 1)
            else:
                raise Exception("Contrast Estimate: Unknown stat %s" % contrast.stat)
        script += "jobs{3}.stats{1}.con.consess = consess;\n"
        script += "if strcmp(spm('ver'),'SPM8'), spm_jobman('initcfg');jobs=spm_jobman('spm5tospm8',{jobs});end\n"
        script += "spm_jobman('run',jobs);\n"

        return script

    def _list_outputs(self):
        outputs = self._outputs().get()
        pth = os.getcwd()
        spm = sio.loadmat(os.path.join(pth, 'SPM.mat'))
        con_images = []
        spmT_images = []
        for con in spm['SPM'][0, 0].xCon[0]:
            con_images.append(str(os.path.join(pth, con.Vcon[0, 0].fname[0])))
            spmT_images.append(str(os.path.join(pth, con.Vspm[0, 0].fname[0])))
        if con_images:
            outputs['con_images'] = con_images
            outputs['spmT_images'] = spmT_images
        ess = glob(os.path.join(pth, 'ess*.img'))
        if len(ess) > 0:
            outputs['ess_images'] = sorted(ess)
        spmf = glob(os.path.join(pth, 'spmF*.img'))
        if len(spmf) > 0:
            outputs['spmF_images'] = sorted(spmf)
        outputs['spm_mat_file'] = self.inputs.spm_mat_file
        return outputs

class ThresholdInputSpec(SPMCommandInputSpec):
    spm_mat_file = File(exists=True, desc='absolute path to SPM.mat', copyfile=True, mandatory=True)
    spmT_images = InputMultiPath(File(exists=True), desc='stat images from a t-contrast', copyfile=False, mandatory=True)
    mask_image = File(exists=True, desc='binary mask to constrain estimation', copyfile=False, mandatory=True)
    beta_images = InputMultiPath(File(exists=True), desc='design parameter estimates', copyfile=False, mandatory=True)
    residual_image = File(exists=True, desc='Mean-squared image of the residuals', copyfile=False, mandatory=True)
    RPVimage = File(exists=True, desc='Resels per voxel image', copyfile=False, mandatory=True)
    contrast_index = traits.Int(mandatory=True, desc='which contrast (T map) to use')
    use_fwe_correction = traits.Bool(True, usedefault=True, desc="whether to use FWE (Bonferroni) correction for initial threshold")
    height_threshold = traits.Float(0.05, usedefault=True, desc="p-value for initial thresholding (defining clusters)")
    extent_threshold = traits.Int(0, usedefault=True, desc='minimum cluster size')
    extent_fdr_p_threshold = traits.Float(0.05, usedefault=True, desc='p threshold on FDR corrected cluster size probabilities')

class ThresholdOutputSpec(TraitedSpec):
    thresholded_map = File(exists=True)


class Threshold(SPMCommand):
    '''Topological FDR thresholding based on cluster extent/size. Smoothness is
    estimated from GLM residuals but is assumed to be the same for all of the
    voxels.
    
    Examples
    --------

    >>> thresh = Threshold()
    >>> thresh.inputs.spm_mat_file = '../../emptydata/SPM.mat'
    >>> thresh.inputs.spmT_images = '../../emptydata/spmT_0001.img'
    >>> thresh.inputs.contrast_index = 1
    >>> thresh.inputs.extent_fdr_p_threshold = 0.05
    >>> thresh.run() # doctest: +SKIP
    '''
    input_spec = ThresholdInputSpec
    output_spec = ThresholdOutputSpec

    def _make_matlab_command(self, _):
        script = "xSPM.swd = '%s';\n" % os.getcwd()
        script += "xSPM.Ic = %d;\n" % self.inputs.contrast_index
        script += "xSPM.u = %f;\n" % self.inputs.height_threshold
        script += "xSPM.Im = [];\n"

        if self.inputs.use_fwe_correction:
            script += "xSPM.thresDesc  = 'FWE';\n"
        else:
            script += "xSPM.thresDesc  = 'none';\n"

        script += "xSPM.k = %d;\n" % self.inputs.extent_threshold
        script += "xSPM.title = 'foo';\n"
        script += "p_thresh = %f;\n" % self.inputs.extent_fdr_p_threshold

        script += """[SPM,xSPM] = spm_getSPM(xSPM);
% checking if anything survived initial thresholding
if isempty(xSPM.XYZ)
    thresholded_XYZ = [];
    thresholded_Z = [];
else
    FWHM  = xSPM.FWHM;
    if FWHM(3) == Inf
        V2R   = 1/prod(FWHM(1:2));
    else
        V2R   = 1/prod(FWHM);
    end;
    
    QPc = xSPM.Pc;
    QPc = sort(QPc(:));
    
    voxel_labels = spm_clusters(xSPM.XYZ);
    nclusters = max(voxel_labels);
    
    thresholded_XYZ = [];
    thresholded_Z = [];
    
    for i = 1:nclusters
       cluster_size = sum(voxel_labels==i);
       cluster_size_resels = cluster_size*V2R;
       p = spm_P_clusterFDR(cluster_size_resels,xSPM.df,xSPM.STAT,xSPM.R,xSPM.n,xSPM.u,QPc);
       if p < p_thresh
           thresholded_XYZ = cat(2, thresholded_XYZ, xSPM.XYZ(:,voxel_labels == i));
           thresholded_Z = cat(2, thresholded_Z, xSPM.Z(voxel_labels == i));
       end
    end
end
% workaround to write an empty volume
if isempty(thresholded_XYZ)
    thresholded_Z = [0];
    thresholded_XYZ = [1 1 1]';
end
"""

        script += "spm_write_filtered(thresholded_Z,thresholded_XYZ,xSPM.DIM,xSPM.M,'foo', '%s');\n" % os.path.abspath('thresholded_map.hdr')

        return script

    def _list_outputs(self):
        outputs = self._outputs().get()
        outputs['thresholded_map'] = os.path.abspath('thresholded_map.img')
        return outputs


class FactorialDesignInputSpec(SPMCommandInputSpec):
    spm_mat_dir = Directory(exists=True, field='dir', desc='directory to store SPM.mat file (opt)')
    # really need to make an alias of InputMultiPath because the inputs below are not Path
    covariates = InputMultiPath(traits.Dict(key_trait=traits.Enum('vector', 'name',
                                                                  'interaction', 'centering')),
                                field='cov',
                                desc='covariate dictionary {vector, name, interaction, centering}')
    threshold_mask_none = traits.Bool(field='masking.tm.tm_none',
                                      xor=['threshold_mask_absolute', 'threshold_mask_relative'],
                                      desc='do not use threshold masking')
    threshold_mask_absolute = traits.Float(field='masking.tm.tma.athresh',
                                      xor=['threshold_mask_none', 'threshold_mask_relative'],
                                      desc='use an absolute threshold')
    threshold_mask_absolute = traits.Float(field='masking.tm.tmr.rthresh',
                                      xor=['threshold_mask_absolute', 'threshold_mask_none'],
                                      desc='threshold using a proportion of the global value')
    use_implicit_threshold = traits.Bool(field='masking.im',
                                         desc='use implicit mask NaNs or zeros to threshold')
    explicit_mask_file = File(field='masking.em', #requires cell
                              desc='use an implicit mask file to threshold')
    global_calc_omit = traits.Bool(field='globalc.g_omit',
                                   xor =['global_calc_mean', 'global_calc_values'],
                                   desc='omit global calculation')
    global_calc_mean = traits.Bool(field='globalc.g_mean',
                                   xor =['global_calc_omit', 'global_calc_values'],
                                   desc='use mean for global calculation')
    global_calc_values = traits.List(traits.Float, field='globalc.g_user.global_uval',
                              xor =['global_calc_mean', 'global_calc_omit'],
                              desc='omit global calculation')
    no_grand_mean_scaling = traits.Bool(field='globalm.gmsca.gmsca_no',
                                    desc='do not perform grand mean scaling')
    global_normalization = traits.Enum(1,2,3,field='globalm.glonorm',
                                    desc='global normalization None-1, Proportional-2, ANCOVA-3')

class FactorialDesignOutputSpec(TraitedSpec):
    spm_mat_file = File(exists=True, desc='SPM mat file')

class FactorialDesign(SPMCommand):
    """Base class for factorial designs
    
    http://www.fil.ion.ucl.ac.uk/spm/doc/manual.pdf#page=79
    
    """

    input_spec = FactorialDesignInputSpec
    output_spec = FactorialDesignOutputSpec        
    _jobtype = 'stats'
    _jobname = 'factorial_design'

    def _format_arg(self, opt, spec, val):
        """Convert input to appropriate format for spm
        """
        if opt in ['spm_mat_dir']:
            return np.array([str(val)], dtype=object)
        if opt in ['covariates']:
            outlist = []
            mapping = {'name':'cname','vector':'c',
                       'interactions':'iCFI',
                       'centering':'iCC'}
            for dictitem in val:
                outdict = {}
                for key,keyval in dictitem.items():
                    outdict[mapping[key]] = keyval
                outlist.append(outdict)
            print outlist
            return outlist
        return val

    def _parse_inputs(self):
        """validate spm realign options if set to None ignore
        """
        einputs = super(FactorialDesign, self)._parse_inputs()
        if not isdefined(self.inputs.spm_mat_dir):
            einputs[0]['dir'] = np.array([str(os.getcwd())], dtype=object)
        return einputs
    
    def _list_outputs(self):
        outputs = self._outputs().get()
        spm = os.path.join(os.getcwd(), 'SPM.mat')
        outputs['spm_mat_file'] = spm
        return outputs
    
class OneSampleTTestDesignInputSpec(FactorialDesignInputSpec):
    in_files = traits.List(File(exists=True), field='des.t1.scans',
                           mandatory=True, minlen=2,
                           desc='input files')

class OneSampleTTestDesign(FactorialDesign):
    """Create SPM design for one sample t-test
    
    >>> ttest = OneSampleTTestDesign()
    >>> ttest.inputs.in_files = ['../../emptydata/cont1.nii', '../../emptydata/cont2.nii']
    >>> ttest.run() # doctest: +SKIP
    """
    
    input_spec = OneSampleTTestDesignInputSpec
    
    def _format_arg(self, opt, spec, val):
        """Convert input to appropriate format for spm
        """
        if opt in ['in_files']:
            return np.array(val, dtype=object)
        return super(OneSampleTTestDesign, self)._format_arg(opt, spec, val)

class TwoSampleTTestDesignInputSpec(FactorialDesignInputSpec):
    # very unlikely that you will have a single image in one group, so setting
    # parameters to require at least two files in each group [SG]
    group1_files = traits.List(File(exists=True), field='des.t2.scans1',
                               mandatory=True, minlen=2,
                               desc='Group 1 input files')
    group2_files = traits.List(File(exists=True), field='des.t2.scans2',
                               mandatory=True, minlen=2,
                               desc='Group 2 input files')
    dependent = traits.Bool(field='des.t2.dept',
                            desc='Are the measurements dependent between levels')
    unequal_variance = traits.Bool(field='des.t2.variance',
                                   desc='Are the variances equal or unequal between groups')

class TwoSampleTTestDesign(FactorialDesign):
    """Create SPM design for two sample t-test
    
    >>> ttest = TwoSampleTTestDesign()
    >>> ttest.inputs.group1_files = ['../../emptydata/cont1.nii', '../../emptydata/cont2.nii']
    >>> ttest.inputs.group2_files = ['../../emptydata/cont1a.nii', '../../emptydata/cont2a.nii']
    >>> ttest.run() # doctest: +SKIP
    """
    
    input_spec = TwoSampleTTestDesignInputSpec

    def _format_arg(self, opt, spec, val):
        """Convert input to appropriate format for spm
        """
        if opt in ['group1_files', 'group2_files']:
            return np.array(val, dtype=object)
        return super(TwoSampleTTestDesign, self)._format_arg(opt, spec, val)
