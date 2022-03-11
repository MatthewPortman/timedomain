try:
    from sncosmo.models import Model
except ImportError as e:
    import pip
    pip.main(['install', '--user', 'sncosmo'])

    from sncosmo.models import Model
except Exception as x:
    print(x)
    raise SystemExit

from desisim.scripts.quickspectra import sim_spectra
from desispec.io import read_spectra, write_spectra
from desispec.spectra import stack as specstack
from desispec.coaddition import coadd, coadd_cameras, spectroperf_resample_spectra
from desispec.resolution import Resolution
from desispec.interpolation import resample_flux

from speclite import filters
rfilt = filters.load_filters('decam2014-r')

import redrock.templates

from astropy import units as u
from astropy.table import Table, vstack, join

import os
from glob import glob

from scipy.ndimage import gaussian_filter

from argparse import ArgumentParser, ArgumentDefaultsHelpFormatter

from copy import copy

import numpy as np
import matplotlib as mpl
import matplotlib.pyplot as plt

import argparse


class TransientModels:

    def __init__(self, modelfits='/global/project/projectdirs/desi/science/td/timedomain-github/snmodels/vincenzi_2019_models.fits'):
        """Generate a dictionary of Type Ia and CCSN model types in sncosmo.
        
        Parameters
        ----------
        modelfits : str
            Path to FITS file listing CCSN models.
        """
        modtab = Table.read(modelfits)
        # List of models with odd-looking spectra for some epochs (provided by Eddie Sepeku).
        blacklist = ['SN2013by','SN2013fs','SN2009bw','SN2012aw','SN2009kr','ASASSN14jb','SN2013am','SN2008ax','SN2008fq','SN2009ip','IPTF13bvn','SN2008D','SN1994I', 'SN2007gr','SN2009bb','SN2007ru']
        select = [x not in blacklist for x in modtab['Name']]
        modtab = modtab[select]
        sntypes = np.unique(modtab['Type'])
        self.models = {}
        for sntype in sntypes:
            self.models[sntype] = []
            for nm in modtab['Name'][modtab['Type'] == sntype]:
                # Get the corresponding sncosmo model with host dust correction applied.
                if nm.startswith('SN'):
                    model_name = 'v19-{}-corr'.format(nm[2:].lower())
                else:
                    model_name = 'v19-{}-corr'.format(nm.lower())
                self.models[sntype].append(model_name)

        # Add Ia models.
        self.models['Ia'] = ['hsiao']
        
    def calc_flux(self, z, t, model, obswave, return_range=False):
        """Compute flux from an sncosmo model given redshift and time.

        Parameters
        ----------
        z : float
            Source redshift.
        t : float
            Source phase / epoch (usually time to max light in days).
        model : sncosmo.models.Model
            Model with builtin flux.
        obswave : ndarray
            Input array of observed wavelengths.
        return_range : bool
            If true, return model min and max wavelength.

        Returns
        -------
        flux : ndarray
            Model flux calculated vs obswave.
        minwave : float (optional)
            Minimum wavelength of model given redshift z.
        maxwave : float (optional)
            Maximum wavelength of model given redshift z.
        """
        model.set(z=z, t0=0)
        select = (obswave >= model.minwave()) & (obswave <= model.maxwave())
        flux = np.zeros_like(obswave, dtype=float)
        flux[select] = model.flux(t, obswave[select])
        if return_range:
            return flux, model.minwave(), model.maxwave()
        return flux
        
    def get_random_model(self, select_from_type=None):
        """Select a random SN model from the list of registered models.
        
        Parameters
        ----------
        select_from_type : str or list
            List of desired types (e.g., ['Ia', 'II', ...].
            
        Returns
        -------
        modelname : str
            Name of sncosmo model selected.
        """
        # Select the SN type.
        sntype = None
        while sntype is None:
            t = np.random.choice(list(self.models.keys()))
            if select_from_type is not None:
                if isinstance(select_from_type, list):
                    if t in select_from_type:
                        sntype = t
                else:
                    if t == select_from_type:
                        sntype = t
            else:
                sntype = t
                    
        # Select the supernova model.
        return sntype, np.random.choice(self.models[sntype])
    
    def get_random_phase(self, minphase=-10, maxphase=30):
        """Return min/max phase to simulate, relative to t=0, peak emission.
        
        Parameters
        ----------
        minphase : int
            Minimum number of days before t=0.
        maxphase : int
            Maximum number of days before t=0.
            
        Returns
        -------
        phase : int
            Phase of explosion, in days relative to t=0.
        """
        return np.random.randint(minphase, maxphase+1)
        
    def simulate_spectra(self, z, obswave=np.arange(3300, 10991), select_from_type=['Ia', 'Ib', 'Ic', 'II', 'IIb'], phase_range=[-10,30]):
        """Generate a realized spectrom from a random model at redshift z.
        
        Parameters
        ----------
        z : float or list or ndarray
            Redshift of the transient(s).
        obswave : list or ndarray
            Observation wavelengths, in angstroms.
        select_from_type : str or list
            List of desired types (e.g., ['Ia', 'II', ...].
        phase_range: list
            Min/max explosion phases to simulate.
        
        Returns
        -------
        sntype : str or list
            Supernova type simulated.
        snmodel : str or list
            Name of model used for simulation.
        phase : float or list
            Phase(s) of transient in simulation.
        flux : list or ndarray
            A single spectrum or list of spectra (one for each input redshift).
        """
        # Generate a single spectrum.
        if np.isscalar(z):
            sntype, snmodel = self.get_random_model(select_from_type)
            phase = self.get_random_phase(phase_range[0], phase_range[1])
            model = Model(snmodel)
            flux = self.calc_flux(z, phase, model, obswave)
        # Generate an array of spectra.
        else:
            sntype, snmodel, phase, flux = [], [], [], []
            for _z in z:
                _sntype, _snmodel = self.get_random_model(select_from_type)
                _phase = self.get_random_phase(phase_range[0], phase_range[1])
                model = Model(_snmodel)
                _flux = self.calc_flux(_z, _phase, model, obswave)
                sntype.append(_sntype)
                snmodel.append(_snmodel)
                phase.append(_phase)
                flux.append(_flux)
                
        return sntype, snmodel, phase, flux


class ExposureData:
    
    def __init__(self, prefix=os.environ['DESI_SPECTRO_REDUX'], redux='fuji'):
        """Build an exposure table with observing conditions using Aaron Meisner's
        exposure conditions database.

        Parameters
        ----------
        redux : str
            Spectroscopic reduction (denali, everest, fuji, ...).

        Returns
        -------
        exptab : astropy.Table
            Table of exposure conditions.
        """
        self.redux = redux
        self.prefix = os.environ['DESI_SPECTRO_REDUX']
        
        # Grab conditions database.
        matched_cond_files = sorted(glob('/global/cfs/cdirs/desi/users/ameisner/GFA/conditions/offline_matched_coadd_ccds_SV1-thru_*fits'))
        obsconditions = Table.read(matched_cond_files[-1], 3)

        # Loop through all exposure tables corresponding to the chosen pipeline reduction (denali, everest, fuji, ...)
        exptab_redux = None
        exposure_tables = sorted(glob('{}/{}/exposure_tables/2*/*.csv'.format(self.prefix, self.redux)))
        columns = ['EXPID', 'EXPTIME', 'OBSTYPE', 'TILEID', 'FA_SURV', 'PROGRAM', 'TARGTRA', 'TARGTDEC', 'NIGHT']

        for i, exposure_table in enumerate(exposure_tables):
            exptab = Table.read(exposure_table, format='csv')[columns]

            # Remove rows where FA_SURV and PROGRAM are masked.
            select = np.ones(len(exptab), dtype=bool)
            if hasattr(exptab['PROGRAM'], 'mask'):
                select = select & ~exptab['PROGRAM'].mask
            if hasattr(exptab['FA_SURV'], 'mask'):
                select = select & ~exptab['FA_SURV'].mask
            exptab = exptab[select]

            if np.any(select):
                select = exptab['PROGRAM'] == 'bright'
                exptab = exptab[select]

                if exptab_redux is None:
                    exptab_redux = exptab
                else:
                    exptab_redux = vstack([exptab_redux, exptab])

        obscolumns = ['EXPID', 'SKYRA', 'SKYDEC', 'MOON_ILLUMINATION', 'MOON_ZD_DEG', 'MOON_SEP_DEG',
                      'MJD', 'FWHM_ASEC', 'TRANSPARENCY', 'SKY_MAG_AB',
                      'FIBER_FRACFLUX', 'FIBER_FRACFLUX_ELG', 'FIBER_FRACFLUX_BGS',
                      'AIRMASS', 'RADPROF_FWHM_ASEC',
                      'FIBERFAC', 'FIBERFAC_ELG', 'FIBERFAC_BGS']

        self.exptab = join(exptab_redux, obsconditions[obscolumns], keys=['EXPID'])
        self.specfiles = self._get_spectra_files()
        
        self.tiles = [*self.specfiles]
        self.nights = [[*self.specfiles[t]][0] for t in self.tiles]
        self.nspec = 0
        
    def __iter__(self):
        """Iterator through spectra and redshift files on disk.
        """
        self.tileidx = 0
        self.fileidx = 0
        return self
    
    def __next__(self):
        """Step through tile data for each night, using the petals as 'chunks' in the iteration.
        """
        try:
            tile = self.tiles[self.tileidx]
            night = self.nights[self.tileidx]
            self.fileidx += 1
            try:
                cofile = self.specfiles[tile][night]['coadds'][self.fileidx-1]
                rdfile = self.specfiles[tile][night]['redshifts'][self.fileidx-1]
                
                # return tile, night, cofile
                obscond = self._get_conditions(tile, night)
                spec, ztab = self._get_spectraz(cofile, rdfile)
                
                return cofile, obscond, spec, ztab
            except IndexError:
                self.fileidx = 0
                self.tileidx += 1
                return self.__next__()
            
        except IndexError:
            raise StopIteration
        
    def _get_spectra_files(self, program='SV2', grouping='pernight'):
        """Get list of coadds and redrock files for a given reduction, program, and grouping.

        Parameters
        ----------
        program : str
            Name of observing program ('SV1', 'SV2', 'MAIN', ...).
        grouping : str
            Exposure grouping: pernight or cumulative.

        Returns
        -------
        specfiles : dict
            Dictionary of tiles and per-night data added across exposures.
        """
        select = self.exptab['FA_SURV'] == program.lower()
        tileids = np.unique(self.exptab['TILEID'][select])
        specfiles = {}
        for tileid in tileids:
            specfiles[tileid] = {}
            tilefolder = f'{self.prefix}/{self.redux}/tiles/{grouping}/{tileid}'
            nightfolders = sorted(glob(f'{tilefolder}/*'))
            for nightfolder in nightfolders:
                night = int(os.path.basename(nightfolder))
                coadds = sorted(glob(f'{nightfolder}/coadd*.fits'))
                redshifts = [x.replace('coadd', 'redrock') for x in coadds]
                specfiles[tileid][night] = { 'coadds': coadds, 'redshifts': redshifts }
        return specfiles
    
    def _get_conditions(self, tileid, night):
        """Get a dictionary of observing conditions for a given tile on a given night.
        If there are several exposures, pick the conditions for the first exposure.
        
        Parameters
        ----------
        tileid : int
            Tile ID.
        night : int
            Night in YYYYMMDD format.
            
        Returns
        -------
        obscond : dict
            Dictionary of observing conditions from the exposures table.
        """
        exposure_data = self.exptab[(self.exptab['TILEID'] == tileid) & (self.exptab['NIGHT'] == night)]
        obsconditions = {
            'SEEING'   : np.average(exposure_data['FWHM_ASEC']),
            'EXPTIME'  : np.sum(exposure_data['EXPTIME']),
            'AIRMASS'  : np.average(exposure_data['AIRMASS']),
            'MOONFRAC' : np.average(exposure_data['MOON_ILLUMINATION']),
            'MOONALT'  : np.average(exposure_data['MOON_ZD_DEG']),
            'MOONSEP'  : np.average(exposure_data['MOON_SEP_DEG'])
        }
        return obsconditions
                      
    def _get_spectraz(self, coadd_file, redshift_file):
        """Get coadded spectra and redshifts for a given night.
        
        Parameters
        ----------
        coadds : list
            List of coadd FITS files.
        redshifts : list
            List of redshift files.
            
        Returns
        -------
        spectra : desispec.Spectra
            Spectra from a given set of coadd FITS files.
        """
        spec = read_spectra(coadd_file)
        select_s = spec.fibermap['OBJTYPE'] == 'TGT'
        spec = spec[select_s]
        spec_targid = spec.fibermap['TARGETID']

        ztab = Table.read(redshift_file, 'REDSHIFTS')
        select_z = (ztab['ZWARN']==0) & (ztab['DELTACHI2']>=25) & (ztab['SPECTYPE']=='GALAXY')
        ztab = ztab[select_z]
        ztab_targid = ztab['TARGETID']
            
        i = np.isin(spec_targid, ztab_targid)
        j = np.isin(ztab_targid, spec_targid)

        return spec[i], ztab[j]
    

if __name__ == '__main__':

    p = argparse.ArgumentParser(description='Transient simulations from data',
                                formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    p.add_argument('--nsim', dest='nsim', default=1000, type=int,
                   help='Minimum number of spectra to generate.')
    p.add_argument('--out-prefix', dest='outpref', type=str,
                   help='Default folder of output spectra FITS files.',
                   default='/global/project/projectdirs/desi/science/td/sim/sv1')
    args = p.parse_args()

    # Initialize redrock templates.
    templates = dict()
    for f in redrock.templates.find_templates():
        t = redrock.templates.Template(f)
        templates[(t.template_type, t.sub_type)] = t

    # Initialize access to transient models.
    transmod = TransientModels()
    wavemodel = np.arange(3300, 10991)
    sim_types = ['Ia', 'Ib', 'Ic', 'II', 'IIb']
    ntypes = len(sim_types) + 1

    # Generate list of exposures.
    exposures = ExposureData()

    # Loop through exposures tile by tile and petal by petal.
    nsim = 0
    for i, (cofile, obs, spec, ztab) in enumerate(exposures):
        print(obs)
        print(spec.num_spectra(), len(ztab))

        # Generate transient models for some fraction of the input spectra.
        nhosts = spec.num_spectra() // ntypes
        ntrans = spec.num_spectra() - nhosts
        types, models, phases, trafluxes = transmod.simulate_spectra(z=ztab['Z'][nhosts:], select_from_type=sim_types, obswave=wavemodel)

        # Rescale the transient spectra to the underlying data in the r band.
        delta_r = np.random.uniform(0, 5, size=ntrans)
        rfluxratio = 10**(-delta_r/2.5)

        for i in range(ntrans):
            # Extract the redrock fit coefficients and reconstruct the redrock spectrum.
            targetid, z, sp, sb, coeff = ztab[nhosts + i][['TARGETID', 'Z', 'SPECTYPE', 'SUBTYPE', 'COEFF']]
            if np.ma.is_masked(sb):
                sb = ''
            ncoeff = templates[(sp, sb)].flux.shape[0]
            coeff = coeff[0:ncoeff]

            tflux = templates[(sp, sb)].flux.T.dot(coeff)
            twave = templates[(sp, sb)].wave * (1 + z)

            # Resample the redrock flux to the wavelength range of the transient simulation.
            txflux = resample_flux(wavemodel, twave, tflux, ivar=None, extrapolate=False)

            # Scale the transient flux to the host flux (given by the redrock model rather than the actual spectrum).
            galnorm = rfilt.get_ab_maggies(txflux, copy(wavemodel))
            fluxr_gal = galnorm['decam2014-r'].data[0]

            tranorm = rfilt.get_ab_maggies(trafluxes[i], copy(wavemodel))
            fluxr_tra = tranorm['decam2014-r'].data[0]

            trafactor = fluxr_gal * rfluxratio[i] / fluxr_tra
            trafluxes[i] *= trafactor

        # Simulate the transient spectra using the current obsconditions.
        outfits = os.path.basename(cofile).replace('coadd', 'transim')
        outfits = os.path.join(args.outpref, outfits)

        sim_spectra(wavemodel, np.asarray(trafluxes), program='bright',
            spectra_filename=outfits,
            obsconditions=obs,
            targetid=ztab['TARGETID'][nhosts:])

        # Read the simulated transient spectra and resample to data wavelength.
        a = coadd_cameras(spec[:nhosts])
        a.fibermap.rename_column('COADD_FIBERSTATUS', 'FIBERSTATUS')
        a.scores = None

        simspec = read_spectra(outfits)
        b = spectroperf_resample_spectra(simspec, a.wave['brz'], nproc=2)
        b.fibermap = spec.fibermap[nhosts:]
        b.fibermap.rename_column('COADD_FIBERSTATUS', 'FIBERSTATUS')
        idx = np.isin(spec.exp_fibermap['TARGETID'], simspec.fibermap['TARGETID'])
        b.exp_fibermap = spec.exp_fibermap[idx]
        b.scores = None

        # Set up new output spectra. Store metadata in extra_catalog.
        c = specstack([a, b])
        extra_catalog = Table()
        extra_catalog['TYPE'] = nhosts * ['Host'] + types
        extra_catalog['MODEL'] = nhosts * [''] + models
        extra_catalog['PHASE'] = nhosts * [0] + phases
        extra_catalog['RFLUXRATIO'] = nhosts * [0.] + list(rfluxratio)
        extra_catalog['Z'] = ztab['Z']
        c.extra_catalog = extra_catalog

        # Write output.
        write_spectra(outfits, c)

        nsim += c.num_spectra()
        if nsim >= args.nsim:
            print(f'Simulated {nsim} spectra. Stopping.')
            break

