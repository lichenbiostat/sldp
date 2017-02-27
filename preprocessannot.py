from __future__ import print_function, division
import argparse, os, gzip, gc, time
import numpy as np
import pandas as pd
import itertools as it
from pybedtools import BedTool
import pyutils.pretty as pretty
import pyutils.fs as fs
import pyutils.iter as pyit
import gprim.annotation as ga
import gprim.dataset as gd
import pyutils.memo as memo
import weights


def main(args):
    # basic initialization
    mhc = [25684587, 35455756]
    refpanel = gd.Dataset(args.bfile_chr)
    annots = [ga.Annotation(annot) for annot in args.sannot_chr]
    baselineannots = [ga.Annotation(annot) for annot in args.baseline_sannot_chr]

    # read in ld blocks, remove MHC, read SNPs to print
    ldblocks = pd.read_csv(args.ld_blocks, delim_whitespace=True, header=None,
            names=['chr','start', 'end'])
    mhcblocks = (ldblocks.chr == 'chr6') & (ldblocks.end > mhc[0]) & (ldblocks.start < mhc[1])
    ldblocks = ldblocks[~mhcblocks]
    print(len(ldblocks), 'loci after removing MHC')
    print_snps = pd.read_csv(args.print_snps, header=None, names=['SNP'])
    print_snps['printsnp'] = True
    print(len(print_snps), 'print snps')

    for annot in annots:
        t0 = time.time()
        for c in args.chroms:
            print(time.time()-t0, ': loading chr', c, 'of', args.chroms)

            # get refpanel snp metadata for this chromosome
            snps = refpanel.bim_df(c)
            snps = ga.smart_merge(snps, refpanel.frq_df(c)[['SNP','MAF']])
            print(len(snps), 'snps in refpanel', len(snps.columns), 'columns, including metadata')

            # read in annotation
            print('reading baseline annotations (assuming theyre already synced to refpanel')
            baseline_names = []
            for bannot in baselineannots:
                mynames = bannot.names(22)
                baseline_names += mynames # names of annotations
                print(time.time()-t0, ': reading annot', annot.filestem())
                snps = pd.concat([snps, bannot.sannot_df(c)[mynames]], axis=1)
            print('reading annot', annot.filestem())
            names = annot.names(c) # names of annotations
            allnames = baseline_names + names
            namesR = [n+'.R' for n in names] # names of results
            a = annot.sannot_df(c)
            if 'SNP' in a.columns:
                print('not a thinannot => doing full reconciliation of snps and allele coding')
                if any(a.SNP != snps.SNP):
                    print('ERROR: refpanel and annot need to have same SNPs in same order')
                    exit()
                snps = ga.reconciled_to(snps, a, names, missing_val=0)
            else:
                print('detected thinannot, so assuming that annotation is synched to refpanel')
                snps = pd.concat([snps, a[names]], axis=1)

            # add information on which snps to print
            print('merging in print_snps')
            snps = pd.merge(snps, print_snps, how='left', on='SNP')
            snps.printsnp.fillna(False, inplace=True)

            # put on per-normalized-genotype scale
            if not args.per_norm_genotype:
                print('adjusting for maf')
                snps[names] = snps[names]*np.sqrt(2*snps.MAF.values*(1-snps.MAF.values))[:,None]

            snps = pd.concat([snps, pd.DataFrame(np.zeros(a[names].shape), columns=namesR)], axis=1)

            for ldblock, X, meta, ind in refpanel.block_data(ldblocks, c, meta=snps):
                if meta.printsnp.sum() == 0:
                    print('no print-snps in this block')
                    continue
                print(meta.printsnp.sum(), 'print-snps')
                if (meta[names] == 0).values.all():
                    print('annotations are all 0 in this block')
                    snps.loc[ind, namesR] = 0
                    VTRV = pd.DataFrame(columns=allnames, index=allnames).fillna(value=0)
                    VTV = pd.DataFrame(columns=allnames, index=allnames).fillna(value=0)
                else:
                    mask = meta.printsnp.values
                    V = meta[allnames].values
                    XV = X.dot(V)
                    snps.loc[ind[mask], namesR] = \
                            X[:,mask].T.dot(XV[:,-len(names)]) / X.shape[0]
                    VTRV = pd.DataFrame(XV.T.dot(XV)/X.shape[0],
                            columns=allnames, index=allnames)
                    VTV = pd.DataFrame(V.T.dot(V), columns=allnames, index=allnames)
                VTRV.to_csv(annot.filestem()+'VTRV.'+str(ldblock.name), sep='\t')
                VTV.to_csv(annot.filestem()+'VTV.'+str(ldblock.name), sep='\t')

            print('writing output')
            with gzip.open('{}{}.RV.gz'.format(annot.filestem(), c),'w') as f:
                snps.loc[snps.printsnp,namesR].to_csv(
                        f, index=False, sep='\t')
            ldblocks.fillna(value=0).to_csv(
                    annot.filestem()+'ldblocks', index=False, sep='\t')

            del snps; memo.reset(); gc.collect()

    print('done')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--sannot-chr', nargs='+', #required=True,
            default=['/groups/price/yakir/data/annot/basset/processed.a8/8988T/'],
            help='path to sannot.gz files, not including chromosome')
    parser.add_argument('--baseline-sannot-chr', nargs='+', #required=True,
            default=[],
            help='path to sannot.gz files, not including chromosome')
    parser.add_argument('--print-snps',
            default='/groups/price/ldsc/reference_files/1000G_EUR_Phase3/'+\
                    '1000G_hm3_noMHC.rsid',
            help='The set of snps for which to print the processed annot')

    parser.add_argument('-per-norm-genotype', action='store_true', default=False,
            help='assume that v is in units of per normalized genotype rather than per ' +\
                    'allele')
    parser.add_argument('--bfile-chr',
            default='/groups/price/ldsc/reference_files/1000G_EUR_Phase3/plink_files/' + \
                '1000G.EUR.QC.',
            help='path to plink bfile of reference panel to use, not including chrom num')
    parser.add_argument('--ld-blocks',
            default='/groups/price/yakir/data/reference/pickrell_ldblocks.hg19.eur.bed',
            help='path to UCSC bed file containing one bed interval per LD' + \
                    ' block')
    parser.add_argument('--chroms', nargs='+', default=range(1,23), type=int)

    args = parser.parse_args()
    pretty.print_namespace(args)
    main(args)
