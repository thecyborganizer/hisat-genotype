#!/usr/bin/env python
# --------------------------------------------------------------------------- #
# Copyright 2017, Daehwan Kim <infphilo@gmail.com>                            #
#                                                                             #
# This file is part of HISAT-genotype. It contains the core genotyping and    #
# typing functions to run HISAT-genotype.                                     #
#                                                                             #
# HISAT-genotype is free software: you can redistribute it and/or modify      #
# it under the terms of the GNU General Public License as published by        #
# the Free Software Foundation, either version 3 of the License, or           #
# (at your option) any later version.                                         #
#                                                                             #
# HISAT-genotype is distributed in the hope that it will be useful,           #
# but WITHOUT ANY WARRANTY; without even the implied warranty of              #
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the               #
# GNU General Public License for more details.                                #
#                                                                             #
# You should have received a copy of the GNU General Public License           #
# along with HISAT-genotype.  If not, see <http://www.gnu.org/licenses/>.     #
# --------------------------------------------------------------------------- #

import sys
import os
import subprocess
import re
import random
import math
import multiprocessing
import json
from datetime import datetime, date, time
from copy import deepcopy
import hisatgenotype_typing_common as typing_common
import hisatgenotype_assembly_graph as assembly_graph
import hisatgenotype_validation_check as validation_check

""" Flag to turn on file debugging to run sanity checks """
setting_file = '/'.join(os.path.realpath(__file__).split('/')[:-2])\
                    + "/devel/settings.json"
with open(setting_file, "r") as ifi:
    settings = json.load(ifi)

SANITY_CHECK = settings["sanity_check"]

# Needed to check for strings and as compatability with python2
if not hasattr(__builtins__, "basestring"):
    basestring = (str, bytes)

# --------------------------------------------------------------------------- #
# Base functions for handling variants                                        #
# --------------------------------------------------------------------------- #
"""
   var: ['single', 3300, 'G']
   exons: [[301, 373], [504, 822], [1084, 1417], [2019, 2301], ...]
"""
def var_in_exon(var, exons):
    exonic = False
    var_type, var_left, var_data = var
    var_right = var_left
    if var_type == "deletion":
        var_right = var_left + int(var_data) - 1
    for exon_left, exon_right in exons:
        if var_left >= exon_left and var_right <= exon_right:
            return True
    return False

""" Report variant IDs whose var is within exonic regions """
def get_exonic_vars(Vars, exons):
    vars = set()
    for var_id, var in Vars.items():
        var_type, var_left, var_data = var
        var_right = var_left
        if var_type == "deletion":
            var_right = var_left + int(var_data) - 1
        for exon_left, exon_right in exons:
            if var_left >= exon_left and var_right <= exon_right:
                vars.add(var_id)
                
    return vars

# --------------------------------------------------------------------------- #
# Functions for Allele parsing                                                #
# --------------------------------------------------------------------------- #
"""
Get representative alleles among those that share the same exonic sequences
"""
def get_rep_alleles(Links, exon_vars, in_alleles = None):
    allele_vars = {}
    for var, alleles in Links.items():
        if var not in exon_vars:
            continue
        for allele in alleles:
            if in_alleles != None and allele not in in_alleles:
                continue
            if allele not in allele_vars:
                allele_vars[allele] = set()
            allele_vars[allele].add(var)

    allele_groups = {}
    for allele, vars in allele_vars.items():
        vars = '-'.join(vars)
        if vars not in allele_groups:
            allele_groups[vars] = []
        allele_groups[vars].append(allele)

    allele_reps = {} # allele representatives
    allele_rep_groups = {} # allele groups by allele representatives
    for allele_members in allele_groups.values():
        assert len(allele_members) > 0
        allele_rep = allele_members[0]
        allele_rep_groups[allele_rep] = allele_members
        for allele_member in allele_members:
            assert allele_member not in allele_reps
            allele_reps[allele_member] = allele_rep

    return allele_reps, allele_rep_groups
    

""" Correction for sequencing Errors """
def error_correct(ref_seq,
                  read_seq,
                  read_pos,
                  mpileup,
                  Vars,
                  Var_list,
                  cmp_list,
                  debug = False):
    if debug:
        print(cmp_list, 
              file=sys.stderr)
        print(read_seq, 
              file=sys.stderr)

    num_correction = 0
    i = 0
    while i < len(cmp_list):
        type, left, length = cmp_list[i][:3]
        assert length > 0
        if left >= len(ref_seq):
            break
        if type == "match":
            middle_cmp_list = []
            last_j = 0
            for j in range(length):
                if read_pos + j >= len(read_seq) or left + j >= len(ref_seq):
                    continue
                
                read_bp, ref_bp = read_seq[read_pos + j], ref_seq[left + j]
                assert left + j < len(mpileup)
                nt_set = mpileup[left + j][0]
                if len(nt_set) > 0 and read_bp not in nt_set:
                    read_bp = 'N' if len(nt_set) > 1 else nt_set[0]                    
                    read_seq = read_seq[:read_pos + j] \
                                + read_bp \
                                + read_seq[read_pos + j + 1:]
                    assert read_bp != ref_bp
                    new_cmp = ["mismatch", left + j, 1, "unknown"]
                    num_correction += 1
                    if read_bp != 'N':
                        var_idx = typing_common.lower_bound(Var_list, left + j)
                        while var_idx < len(Var_list):
                            var_pos, var_id = Var_list[var_idx]
                            if var_pos > left + j:
                                break
                            if var_pos == left + j:
                                var_type, _, var_data = Vars[var_id]
                                if var_type == "single" and read_bp == var_data:
                                    new_cmp[3] = var_id
                                    break                                                        
                            var_idx += 1
                    if j > last_j:
                        middle_cmp_list.append(["match", 
                                                left + last_j, 
                                                j - last_j])
                    middle_cmp_list.append(new_cmp)
                    last_j = j + 1
            if last_j < length:
                middle_cmp_list.append(["match", 
                                        left + last_j, 
                                        length - last_j])

            assert len(middle_cmp_list) > 0
            cmp_list = cmp_list[:i] + middle_cmp_list + cmp_list[i+1:]
            i += (len(middle_cmp_list) - 1)
        else:
            assert type == "mismatch"
            read_bp, ref_bp = read_seq[read_pos], ref_seq[left]
            assert left < len(mpileup)
            nt_set = mpileup[left][0]

            if debug:
                print((left, read_bp, ref_bp, mpileup[left]), 
                      file=sys.stderr)

            if len(nt_set) > 0 and read_bp not in nt_set:
                read_bp = 'N' if len(nt_set) > 1 else nt_set[0]
                read_seq = read_seq[:read_pos] + read_bp + read_seq[read_pos+1:]
                if read_bp == 'N':
                    cmp_list[i][3] = "unknown"
                elif read_bp == ref_bp:
                    cmp_list[i] = ["match", left, 1]
                    num_correction += 1
                else:
                    cmp_list[i][3] = "unknown"
                    var_idx = typing_common.lower_bound(Var_list, left)
                    while var_idx < len(Var_list):
                        var_pos, var_id = Var_list[var_idx]
                        if var_pos > left:
                            break
                        if var_pos == left:
                            var_type, _, var_data = Vars[var_id]
                            if var_type == "single" and read_bp == var_data:
                                cmp_list[i][3] = var_id
                                break                                                        
                        var_idx += 1

                if debug:
                    print((left, read_bp, ref_bp, mpileup[left]), 
                          file=sys.stderr)
                    print(cmp_list[i], 
                          file=sys.stderr)

        read_pos += length
        i += 1

    # Combine matches
    i = 0
    while i < len(cmp_list):
        type, left, length = cmp_list[i][:3]
        if type == "match" and i + 1 < len(cmp_list):
            type2, left2, length2 = cmp_list[i+1][:3]
            if type2 == "match":
                cmp_list[i] = [type, left, length + length2]
                cmp_list = cmp_list[:i+1] + cmp_list[i+2:]
                continue
        i += 1

    if debug:
        print(cmp_list, 
              file=sys.stderr)
        print(read_seq, 
              file=sys.stderr)
                            
    return cmp_list, read_seq, num_correction

# --------------------------------------------------------------------------- #
# Main function for typing used by genotype function                          #
# --------------------------------------------------------------------------- #
""" This script has many argument. Consider making a class or namespace """
def typing(simulation,
           full_path_base_fname,
           locus_list,
           genotype_genome,
           partial,
           partial_alleles,
           refGenes,
           Genes,
           Gene_names,
           Gene_lengths,
           refGene_loci,
           Vars,
           Var_list,
           Links,
           aligners,
           num_editdist,
           assembly,
           output_base,
           error_correction,
           keep_alignment,
           allow_discordant,
           type_primary_exons,
           remove_low_abundance_alleles,
           display_alleles,
           fastq,
           read_fname,
           alignment_fname,
           num_frag_list,
           read_len,
           fragment_len,
           threads,
           best_alleles,
           verbose,
           assembly_verbose,
           out_dir,
           dbversion,
           output_allele_counts,
           test_i = 0):

    complete    = {"Init Align"    : False,
                   "Locus Process" : False,
                   "Align Return"  : False} # list of completed tasks
    print("got here a", file=sys.stderr);
    base_fname  = full_path_base_fname.split("/")[-1]
    core_fid    = "" # May add to bottom of options
    report_base = '%s/%s-%s.' % (out_dir, output_base, base_fname)
    if simulation:
        test_passed  = {}
        core_fid     = str(test_i + 1)
        report_base += "test-"
    else:
        core_fid = '_'.join(read_fname[0].split('/')[-1].split('.')[:-1])

    report_base += core_fid
    report_file  = open('%s.report' % report_base, "w")

    if verbose or assembly_verbose or simulation:
        msg_out = [sys.stderr, report_file]
    else:
        msg_out = [report_file]
    
    # Add version and command info to all report files
    version_dir = '/'.join(os.path.dirname(__file__).split('/')[:-1])
    hg_version  = open(version_dir + '/VERSION', 'r').read()
    h2_version  = open(version_dir + '/hisat2/VERSION', 'r').read()
    cmd_call    = ' '.join(sys.argv)

    for f_ in msg_out:
        print("# VERSIONS:", 
              file=f_)
        print("# HISAT2 - %s" % h2_version, 
              file=f_)
        print("# HISAT-genotype - %s" % hg_version, 
              file=f_)
        print("# Database - %s" % dbversion, 
              file=f_)
        print("# COMMAND:\n%s" % cmd_call, 
              file=f_)
        # if base_fname == "genome":
        #     print("\t" + locus_list, 
        #           file=f_)
        # else:
        #     print(locus_list)
        #     print("\t" + ' '.join(locus_list), 
        #           file=f_)

    # Begin Alignment for typing
    for aligner, index_type in aligners:
        complete["Init Align"] = True # Initialize alignment
        for f_ in msg_out:
            if index_type == "graph":
                print("\n\t\t%s %s" % (aligner, index_type), 
                      file=f_)
            else:
                print("\n\t\t%s %s" % (aligner, index_type), 
                      file=f_)

        remove_alignment_file = False
        if alignment_fname == "":
            # Align reads, and sort the alignments into a BAM file
            remove_alignment_file = True
            if simulation:
                alignment_fname = "%s_output.bam" % base_fname
            else:
                alignment_fname = "%s.bam" % core_fid

            if genotype_genome != "":
                gegenome = genotype_genome
            else:
                gegenome = (full_path_base_fname + "." + index_type)
            typing_common.align_reads(aligner,
                                      simulation,
                                      gegenome,
                                      index_type,
                                      base_fname,
                                      read_fname,
                                      fastq,
                                      threads,
                                      alignment_fname,
                                      verbose)

        viterbi_calls = {}
        for test_Gene_names in locus_list:
            complete["Locus Process"] = True # Begin locus processing
            if base_fname == "genome":
                if simulation:
                    region_chr, region_left, region_right = test_Gene_names[0]
                else:
                    region_chr, region_left, region_right = test_Gene_names
                gene = "%s:%d-%d" % (region_chr, region_left, region_right)
            else:
                if simulation:
                    gene = test_Gene_names[0].split('*')[0]
                else:
                    gene = test_Gene_names
                
            viterbi_calls[gene] = []
            ref_allele          = refGenes[gene]
            ref_seq             = Genes[gene][ref_allele]
            ref_locus           = refGene_loci[gene]
            ref_exons           = ref_locus[-2]
            ref_primary_exons   = ref_locus[-1]
            novel_var_count     = 0        
            gene_vars           = deepcopy(Vars[gene])
            gene_var_list       = deepcopy(Var_list[gene])
            cur_maxright        = -1
            gene_var_maxrights  = {}

            for var_pos, var_id in gene_var_list:
                var_type, var_pos, var_data = gene_vars[var_id]
                if var_type == "deletion":
                    var_pos = var_pos + int(var_data) - 1
                cur_maxright = max(cur_maxright, var_pos)
                gene_var_maxrights[var_id] = cur_maxright
                    
            var_count = {}
            def add_novel_var(gene_vars,
                              gene_var_list,
                              novel_var_count,
                              var_type,
                              var_pos,
                              var_data):
                var_idx = typing_common.lower_bound(gene_var_list, var_pos)
                while var_idx < len(gene_var_list):
                    pos_, id_ = gene_var_list[var_idx]
                    if pos_ > var_pos:
                        break
                    if pos_ == var_pos:
                        type_, _, data_ = gene_vars[id_]
                        assert type_ != var_type or data_ != var_data
                        if type_ != var_type:
                            if var_type == "insertion":
                                break
                            elif var_type == "single" and type_ == "deletion":
                                break
                        else:
                            if var_data < data_:
                                break
                    var_idx += 1
                var_id = "nv%d" % novel_var_count
                assert var_id not in gene_vars
                gene_vars[var_id] = [var_type, var_pos, var_data]
                gene_var_list.insert(var_idx, [var_pos, var_id])                
                return var_id, novel_var_count + 1

            print("got here 1", file=sys.stderr)
            if not os.path.exists(alignment_fname + ".bai"):
                os.system("samtools index %s" % alignment_fname)
            print("got here 2", file=sys.stderr)
            # Read alignments
            alignview_cmd = ["samtools", "view", alignment_fname]
            base_locus = 0
            alignview_proc: subprocess.Popen[str] = ""
            if genotype_genome != "":
                _, chr, left, right = ref_locus[:4]
                alignview_cmd += ["%s:%d-%d" % (chr, left+1, right+1)]
                base_locus = left

            if index_type == "graph":
                alignview_cmd += [ref_allele]
                mpileup = typing_common.get_mpileup(alignview_cmd,
                                                    ref_seq,
                                                    base_locus,
                                                    gene_vars,
                                                    allow_discordant)

                if base_fname == "codis":
                    pair_interdist = typing_common.get_pair_interdist(alignview_cmd,
                                                                      simulation,
                                                                      verbose)
                else:
                    pair_interdist = None

                print("1----------------{}------------- module".format(alignview_cmd), file=sys.stderr)
                bamview_proc = subprocess.Popen(alignview_cmd,
                                                universal_newlines = True,
                                                stdout = subprocess.PIPE,
                                                stderr = open("/dev/null", 'w'))
                for line in bamview_proc.stdout.read():
                    print("a----{}----".format(line)) 
                bamview_proc.stdout.seek(0)
                print("2----------------{}------------- module".format(bamview_proc.stdout), file=sys.stderr)
                sort_read_cmd = ["sort", "-k", "1,1", "-s"] # -s for stable sorting
                alignview_proc = subprocess.Popen(sort_read_cmd,
                                                  universal_newlines = True,
                                                  stdin  = bamview_proc.stdout,
                                                  stdout = subprocess.PIPE,
                                                  stderr = open("/dev/null", 'w'))
            else:
                alignview_proc = subprocess.Popen(alignview_cmd,
                                                  universal_newlines = True,
                                                  stdout = subprocess.PIPE,
                                                  stderr = open("/dev/null", 'w'))

            outs, errs = alignview_proc.communicate()
            alignview_proc_str = outs
            print("3--{}--".format(alignview_proc_str))
            # List of nodes that represent alleles
            allele_vars = {}
            for _, var_id in gene_var_list:
                if var_id not in Links:
                    continue
                allele_list = Links[var_id]
                for allele_id in allele_list:
                    if allele_id not in Genes[gene]:
                        continue
                    if allele_id not in allele_vars:
                        allele_vars[allele_id] = [var_id]
                    else:
                        allele_vars[allele_id].append(var_id)

            # Extract variants that are within exons
            exon_vars = get_exonic_vars(gene_vars, ref_exons)
            primary_exon_vars = get_exonic_vars(gene_vars, ref_primary_exons)

            # Store de bruijn nodes that represent alleles
            allele_nodes = {}
            def create_allele_node(allele_name):
                if allele_name in allele_nodes:
                    return allele_nodes[allele_name]
                if allele_name in allele_vars:
                    var_ids = allele_vars[allele_name]
                else:
                    var_ids = []
                seq = list(ref_seq)  # sequence that node represents

                # how sequence is related to backbone
                var = ["" for i in range(len(ref_seq))]  
                for var_id in var_ids:
                    assert var_id in gene_vars
                    var_type, var_pos, var_data = gene_vars[var_id]
                    assert var_pos >= 0 and var_pos < len(ref_seq)
                    if var_type == "single":
                        seq[var_pos] = var_data
                        var[var_pos] = var_id
                    elif var_type == "deletion":
                        del_len = int(var_data)
                        assert var_pos + del_len <= len(ref_seq)
                        seq[var_pos:var_pos + del_len] = ['D'] * del_len
                        var[var_pos:var_pos + del_len] = [var_id] * del_len
                    else:
                        # DK - to be implemented for insertions
                        assert var_type == "insertion"

                qual = ' ' * len(seq)
                allele_node = assembly_graph.Node(allele_name,
                                                  0,
                                                  seq,
                                                  qual,
                                                  var,
                                                  ref_seq,
                                                  gene_vars,
                                                  mpileup,
                                                  simulation)
                allele_nodes[allele_name] = allele_node
                return allele_node

            true_allele_nodes = {}
            if simulation:
                for allele_name in test_Gene_names:
                    true_allele_nodes[allele_name] \
                        = create_allele_node(allele_name)

            display_allele_nodes = {} # other alleles to display on pdf
            for display_allele in display_alleles:
                display_allele_nodes[display_allele] \
                    = create_allele_node(display_allele)

            # Assembly graph
            asm_graph = assembly_graph.Graph(ref_seq,
                                             gene_vars,
                                             ref_exons,
                                             ref_primary_exons,
                                             partial_alleles,
                                             true_allele_nodes,
                                             {}, # predicted_allele_nodes
                                             display_allele_nodes,
                                             simulation)

            # Choose allele representives from those 
            # that share the same exonic sequences
            allele_reps, allele_rep_groups \
                = get_rep_alleles(Links, exon_vars)
            allele_rep_set \
                = set(allele_reps.values())

            # Choose allele representives from those 
            # that share the primary exonic sequences
            primary_exon_allele_reps, primary_exon_allele_rep_groups \
                = get_rep_alleles(Links, primary_exon_vars, allele_rep_set)
            primary_exon_allele_rep_set \
                = set(primary_exon_allele_reps.values())

            # Sanity check
            if SANITY_CHECK:
                validation_check.check_repset_inclusion(allele_rep_set,
                                                        allele_reps,
                                                        primary_exon_allele_reps)
                                    
            # For checking alternative alignments near the ends of alignments
            Alts_left, Alts_right = typing_common.get_alternatives(ref_seq,
                                                                   allele_vars,
                                                                   gene_vars,
                                                                   gene_var_list,
                                                                   verbose >= 2)

            # Separates and sorts alternative haplotype list
            def haplotype_alts_list(haplotype_alts, left = True):
                haplotype_list = []
                for haplotype in haplotype_alts.keys():
                    if left:
                        pos = int(haplotype.split('-')[-1])
                    else:
                        pos = int(haplotype.split('-')[0])
                    haplotype_list.append([pos, haplotype])
                return sorted(haplotype_list, key = lambda x: x[0])

            Alts_left_list  = haplotype_alts_list(Alts_left, True)
            Alts_right_list = haplotype_alts_list(Alts_right, False)

            # Count alleles
            Gene_primary_exons_counts = {}
            Gene_primary_exons_cmpt   = {}
            Gene_exons_counts = {}
            Gene_exons_cmpt   = {}
            Gene_counts = {}
            Gene_cmpt   = {}
            num_reads = 0
            num_pairs = 0

            # For debugging purposes
            if simulation and verbose >= 2:
                debug_allele_names = set(test_Gene_names)
            else:
                debug_allele_names = set()

            # Read information
            prev_read_id    = None
            prev_right_pos  = 0
            prev_lines      = []
            left_read_ids   = set()
            right_read_ids  = set()
            single_read_ids = set()
            if index_type == "graph":
                # nodes for reads
                read_nodes = []

                # Get number of alleles read aligns to
                def add_count(count_per_read, ht, add):
                    if base_fname == "genome" and len(count_per_read) == 1:
                        for allele in count_per_read.keys():
                            count_per_read[allele] = add
                        return
                    
                    orig_ht = ht
                    ht = ht.split('-')

                    assert len(ht) >= 2
                    left  = int(ht[0])
                    right = int(ht[-1])
                    assert left <= right

                    ht = ht[1:-1]
                    alleles = set(Genes[gene].keys()) - set([ref_allele])
                    for i in range(len(ht)):
                        var_id = ht[i]
                        if var_id.startswith("nv") or \
                           var_id not in Links:
                            continue
                        alleles &= set(Links[var_id])
                    ht = set(ht)

                    tmp_alleles = set()
                    var_idx = typing_common.lower_bound(gene_var_list, right + 1)
                    var_idx = min(var_idx, len(gene_var_list) - 1)
                    while var_idx >= 0:
                        _, var_id = gene_var_list[var_idx]
                        if var_id.startswith("nv") \
                                or var_id in ht \
                                or var_id not in Links:
                            var_idx -= 1
                            continue
                        if var_id in gene_var_maxrights \
                                and gene_var_maxrights[var_id] < left:
                            break
                        var_type, var_left, var_data = gene_vars[var_id]
                        var_right = var_left
                        if var_type == "deletion":
                            var_right = var_left + int(var_data) - 1
                        if (var_left >= left and var_left <= right) \
                                or (var_right >= left and var_right <= right):
                            tmp_alleles |= set(Links[var_id])
                        var_idx -= 1                        
                    alleles -= tmp_alleles
                    alleles &= set(count_per_read.keys())
                    
                    for allele in alleles:
                        count_per_read[allele] += add

                    return len(alleles)

                # Identify best pairs
                def choose_pairs(left_positive_hts, right_positive_hts):
                    if len(left_positive_hts) > 0 \
                            and len(right_positive_hts) > 0 \
                            and max(len(left_positive_hts), 
                                    len(right_positive_hts)) >= 2:
                        expected_inter_dist = pair_interdist
                            
                        best_diff = sys.maxsize
                        picked = []                                
                        for left_ht_str in left_positive_hts:
                            left_ht = left_ht_str.split('-')
                            l_left, l_right = int(left_ht[0]), int(left_ht[-1])
                            for right_ht_str in right_positive_hts:
                                right_ht = right_ht_str.split('-')
                                r_left  = int(right_ht[0])
                                r_right = int(right_ht[-1])
                                if l_right < r_right:
                                    inter_dist = r_left - l_right - 1
                                else:
                                    inter_dist = l_left - r_right - 1

                                cur_diff = abs(expected_inter_dist - inter_dist)
                                if best_diff > cur_diff:
                                    best_diff = cur_diff
                                    picked = [[left_ht_str, right_ht_str]]
                                elif best_diff == cur_diff:
                                    picked.append([left_ht_str, right_ht_str])

                        assert len(picked) > 0

                        left_positive_hts  = set()
                        right_positive_hts = set()
                        for left_ht_str, right_ht_str in picked:
                            left_positive_hts.add(left_ht_str)
                            right_positive_hts.add(right_ht_str)

                    return left_positive_hts, right_positive_hts

                def get_exon_haplotypes(ht, exons):
                    if len(exons) <= 0:
                        return []
                    
                    debug_ht = deepcopy(ht)
                    ht = ht.split('-')
                    assert len(ht) >= 2
                    ht[0], ht[-1] = int(ht[0]), int(ht[-1])
                    exon_hts = []
                    for e_left, e_right in exons:
                        assert len(ht) >= 2
                        ht_left, ht_right = ht[0], ht[-1]
                        if e_left > ht_right or e_right < ht_left:
                            continue

                        new_ht = deepcopy(ht)
                        if ht_left < e_left:
                            split = False
                            for i in range(1, len(new_ht) - 1):
                                var_id = new_ht[i]
                                type, left, data = gene_vars[var_id]
                                if (type != "deletion" and left >= e_left) \
                                        or (type == "deletion" 
                                                and left - 1 >= e_left):
                                    ht_left = e_left
                                    new_ht = [ht_left] + new_ht[i:]
                                    split = True
                                    break
                                if type == "deletion":
                                    right = left + int(data)
                                    if right >= e_left:
                                        ht_left = right
                                        new_ht = [right] + new_ht[i+1:]
                                        split = True
                                        break
                            if not split:
                                ht_left = e_left
                                new_ht = [ht_left, ht_right]
                        assert ht_left >= e_left
                        if ht_right > e_right:
                            split = False
                            for i in reversed(range(1, len(new_ht) - 1)):
                                var_id = new_ht[i]
                                type, right, data = gene_vars[var_id]
                                if type == "deletion":
                                    right = right + int(data) - 1
                                if (type != "deletion" and right <= e_right) \
                                        or (type == "deletion" 
                                                and right + 1 <= e_right):
                                    ht_right = e_right
                                    new_ht = new_ht[:i+1] + [ht_right]
                                    split = True
                                    break
                                if type == "deletion":
                                    left = right - int(data)
                                    if left <= e_right:
                                        ht_right = left
                                        new_ht = new_ht[:i] + [ht_right]
                                        split = True
                                        break
                            if not split:
                                ht_right = e_right
                                new_ht = [ht_left, ht_right]

                        if len(new_ht) == 2:
                            new_ht = "%d-%d" % (new_ht[0], new_ht[-1])
                        else:
                            assert len(new_ht) > 2
                            new_ht = "%d-%s-%d" % (new_ht[0], 
                                                   '-'.join(new_ht[1:-1]), 
                                                   new_ht[-1])
                        assert ht_left <= ht_right
                        exon_hts.append(new_ht)

                    return exon_hts

                # Positive evidence for left and right reads
                left_positive_hts  = set()
                right_positive_hts = set()
                
                # Cigar regular expression
                cigar_re = re.compile('\d+\w')
                for line in alignview_proc_str:
                    if not complete["Align Return"]: # Confirm alingment return
                        complete["Align Return"] = True
                    line = line.strip()
                    cols = line.split()
                    read_id, flag, chr, pos, mapQ, cigar_str = cols[:6]

                    node_read_id = orig_read_id = read_id
                    if simulation:
                        read_id = read_id.split('|')[0]
                    read_seq  = cols[9]
                    read_qual = cols[10]
                    flag      = int(flag)
                    pos       = int(pos)
                    pos      -= (base_locus + 1)
                    if pos < 0:
                        continue

                    # Unalined? Insurance that nonmaped reads will not be processed
                    if flag & 0x4 != 0:
                        if simulation and verbose >= 2:
                            print("Unaligned")
                            print("\t", line)
                        continue

                    # Concordantly mapped?
                    if flag & 0x2 != 0:
                        concordant = True
                    else:
                        concordant = False

                    NM, Zs, MD, NH = "", "", "", ""
                    for i in range(11, len(cols)):
                        col = cols[i]
                        if col.startswith("Zs"):
                            Zs = col[5:]
                        elif col.startswith("MD"):
                            MD = col[5:]
                        elif col.startswith("NM"):
                            NM = int(col[5:])
                        elif col.startswith("NH"):
                            NH = int(col[5:])

                    if NM > num_editdist:
                        continue

                    # Only consider unique alignment
                    if NH > 1:
                        continue

                    # Concordantly aligned mate pairs
                    if not allow_discordant and not concordant:
                        continue

                    # Add reads to nodes and assign left, right, or discordant
                    is_left_read = flag & 0x40 != 0
                    if is_left_read:            # Left read?
                        if read_id in left_read_ids:
                            continue
                        left_read_ids.add(read_id)
                        if not simulation:
                            node_read_id += '|L'
                    elif flag & 0x80 != 0:      # Right read?
                        if read_id in right_read_ids:
                            continue
                        right_read_ids.add(read_id)
                        if not simulation:
                            node_read_id += '|R'
                    else:
                        assert allow_discordant
                        if read_id in single_read_ids:
                            continue
                        single_read_ids.add(read_id)
                        if not simulation:
                            node_read_id += '|U'

                    if Zs:
                        Zs_str = Zs
                        Zs     = Zs.split(',')             

                    assert MD != ""
                    MD_str_pos = 0
                    MD_len     = 0
                    Zs_pos     = 0
                    Zs_i       = 0
                    for _i in range(len(Zs)):
                        Zs[_i]    = Zs[_i].split('|')
                        Zs[_i][0] = int(Zs[_i][0])
                    if Zs_i < len(Zs):
                        Zs_pos += Zs[Zs_i][0]
                    read_pos  = 0
                    left_pos  = pos
                    right_pos = left_pos
                    cigars    = cigar_re.findall(cigar_str)
                    cigars    = [[cigar[-1], int(cigar[:-1])] for cigar in cigars]
                    cmp_list  = []
                    num_error_correction = 0
                    likely_misalignment  = False

                    # Extract variants w.r.t backbone from CIGAR string
                    softclip = [0, 0]
                    for i in range(len(cigars)):
                        cigar_op, length = cigars[i]
                        if cigar_op == 'M':
                            first       = True
                            MD_len_used = 0
                            cmp_list_i = len(cmp_list)
                            while True:
                                if not first or MD_len == 0:
                                    if MD[MD_str_pos].isdigit():
                                        num = int(MD[MD_str_pos])
                                        MD_str_pos += 1
                                        while MD_str_pos < len(MD):
                                            if MD[MD_str_pos].isdigit():
                                                num = num * 10 + int(MD[MD_str_pos])
                                                MD_str_pos += 1
                                            else:
                                                break
                                        MD_len += num
                                # Insertion or full match followed
                                if MD_len >= length:
                                    MD_len -= length
                                    if length > MD_len_used:
                                        cmp_list.append(["match", 
                                                         right_pos + MD_len_used, 
                                                         length - MD_len_used])
                                    break
                                first       = False
                                read_base   = read_seq[read_pos + MD_len]
                                MD_ref_base = MD[MD_str_pos]
                                MD_str_pos += 1
                                assert MD_ref_base in "ACGT"
                                if MD_len > MD_len_used:
                                    cmp_list.append(["match", 
                                                     right_pos + MD_len_used, 
                                                     MD_len - MD_len_used])

                                _var_id = "unknown"
                                if read_pos + MD_len == Zs_pos and Zs_i < len(Zs):
                                    assert Zs[Zs_i][1] == 'S'
                                    _var_id = Zs[Zs_i][2]
                                    Zs_i   += 1
                                    Zs_pos += 1
                                    if Zs_i < len(Zs):
                                        Zs_pos += Zs[Zs_i][0]
                                else:
                                    # Search for a known (yet not indexed) 
                                    # variant or a novel variant
                                    ref_pos = right_pos + MD_len
                                    var_idx = typing_common.lower_bound(gene_var_list, 
                                                                        ref_pos)
                                    while var_idx < len(gene_var_list):
                                        var_pos, var_id = gene_var_list[var_idx]
                                        if var_pos > ref_pos:
                                            break
                                        if var_pos == ref_pos:
                                            var_type, _, var_data = gene_vars[var_id]
                                            if var_type == "single" \
                                                    and var_data == read_base:
                                                _var_id = var_id
                                                break
                                        var_idx += 1

                                cmp_list.append(["mismatch", 
                                                 right_pos + MD_len, 
                                                 1, 
                                                 _var_id])
                                
                                MD_len_used = MD_len + 1
                                MD_len += 1
                                # Full match
                                if MD_len == length:
                                    MD_len = 0
                                    break

                            # Correction for sequencing errors and 
                            # update for cmp_list
                            if error_correction:
                                assert cmp_list_i < len(cmp_list)
                                name_readID = "aHSQ1008:175:C0JVFACXX:5:1109:17665:21583|L"
                                new_cmp_list, \
                                  read_seq, \
                                  _num_error_correction \
                                    = error_correct(ref_seq,
                                                    read_seq,
                                                    read_pos,
                                                    mpileup,
                                                    gene_vars,
                                                    gene_var_list,
                                                    cmp_list[cmp_list_i:],
                                                    node_read_id == name_readID)
                                cmp_list = cmp_list[:cmp_list_i] + new_cmp_list
                                num_error_correction += _num_error_correction

                        elif cigar_op == 'I':
                            _var_id = "unknown"
                            if read_pos == Zs_pos and Zs_i < len(Zs):
                                assert Zs[Zs_i][1] == 'I'
                                _var_id = Zs[Zs_i][2]
                                Zs_i += 1
                                if Zs_i < len(Zs):
                                    Zs_pos += Zs[Zs_i][0]
                            else:
                                # Search for a known (yet not indexed) 
                                # variant or a novel variant
                                var_idx = typing_common.lower_bound(gene_var_list, 
                                                                    right_pos)
                                while var_idx < len(gene_var_list):
                                    var_pos, var_id = gene_var_list[var_idx]
                                    if var_pos > right_pos:
                                        break
                                    if var_pos == right_pos:
                                        var_type, _, var_data = gene_vars[var_id]
                                        if var_type == "insertion" \
                                                and len(var_data) == length:
                                            _var_id = var_id
                                            break
                                    var_idx += 1                            
                            cmp_list.append(["insertion", 
                                             right_pos, 
                                             length, 
                                             _var_id])
                            if 'N' in read_seq[read_pos:read_pos+length]:
                                likely_misalignment = True
                                
                        elif cigar_op == 'D':
                            if MD[MD_str_pos] == '0':
                                MD_str_pos += 1
                            assert MD[MD_str_pos] == '^'
                            MD_str_pos += 1
                            while MD_str_pos < len(MD):
                                if not MD[MD_str_pos] in "ACGT":
                                    break
                                MD_str_pos += 1
                            _var_id = "unknown"
                            if read_pos == Zs_pos and \
                               Zs_i < len(Zs) and \
                               Zs[Zs_i][1] == 'D':
                                _var_id = Zs[Zs_i][2]
                                Zs_i += 1
                                if Zs_i < len(Zs):
                                    Zs_pos += Zs[Zs_i][0]
                            else:
                                # Search for a known (yet not indexed) variant 
                                # or a novel variant
                                var_idx = typing_common.lower_bound(gene_var_list, 
                                                                    right_pos)
                                while var_idx < len(gene_var_list):
                                    var_pos, var_id = gene_var_list[var_idx]
                                    if var_pos > right_pos:
                                        break
                                    if var_pos == right_pos:
                                        var_type, _, var_data = gene_vars[var_id]
                                        if var_type == "deletion" \
                                                and int(var_data) == length:
                                            _var_id = var_id
                                            break
                                    var_idx += 1

                            cmp_list.append(["deletion", 
                                             right_pos, 
                                             length, 
                                             _var_id])

                            # Check if this deletion is artificial alignment
                            if right_pos < len(mpileup):
                                del_count, nt_count = 0, 0
                                for nt, value in mpileup[right_pos][1].items():
                                    count = value[0]
                                    if nt == 'D':
                                        del_count += count
                                    else:
                                        nt_count += count

                                # DK - debugging purposes
                                if base_fname == "hla":
                                    if del_count * 6 < nt_count: 
                                        likely_misalignment = True
                            
                        elif cigar_op == 'S':
                            if i == 0:
                                softclip[0] = length
                                Zs_pos += length
                            else:
                                assert i + 1 == len(cigars)
                                softclip[1] = length
                        else:                    
                            assert cigar_op == 'N'
                            assert False
                            cmp_list.append(["intron", right_pos, length])

                        if cigar_op in "MND":
                            right_pos += length

                        if cigar_op in "MIS":
                            read_pos += length                    
                    
                    # Remove softclip in cigar and modify read_seq and 
                    # read_qual accordingly
                    if sum(softclip) > 0:
                        if softclip[0] > 0:
                            cigars = cigars[1:]
                            read_seq = read_seq[softclip[0]:]
                            read_qual = read_qual[softclip[0]:]
                        if softclip[1] > 0:
                            cigars = cigars[:-1]
                            read_seq = read_seq[:-softclip[1]]
                            read_qual = read_qual[:-softclip[1]]

                        cigar_str = ""
                        for type, length in cigars:
                            cigar_str += str(length)
                            cigar_str += type

                    # if sum(softclip) > 0: #TODO Examine the purpose of this skip
                    #     continue

                    if right_pos > len(ref_seq):
                        continue

                    if num_error_correction > max(1, num_editdist):
                        continue
                        
                    if likely_misalignment:
                        continue

                    # Add novel variants
                    read_pos = 0
                    for cmp_i in range(len(cmp_list)):
                        type_, pos_, length_ = cmp_list[cmp_i][:3]
                        if type_ != "match":
                            var_id_ = cmp_list[cmp_i][3]
                            if var_id_ == "unknown":
                                add = True
                                if type_ == "mismatch":
                                    data_ = read_seq[read_pos]
                                    if data_ == 'N':
                                        add = False
                                elif type_ == "deletion":
                                    data_ = str(length_)
                                else:
                                    assert type_ == "insertion"
                                    data_ = read_seq[read_pos:read_pos + length_]
                                if add:
                                    if type_ != "mismatch":
                                        type_add = type_
                                    else:
                                        type_add = "single"

                                    var_id_, novel_var_count \
                                        = add_novel_var(gene_vars,
                                                        gene_var_list,
                                                        novel_var_count,
                                                        type_add,
                                                        pos_,
                                                        data_)
                                    cmp_list[cmp_i][3] = var_id_
                            if var_id_ != "unknown":
                                if var_id_ not in var_count:
                                    var_count[var_id_] = 1
                                else:
                                    var_count[var_id_] += 1
                                
                        if type_ != "deletion":
                            read_pos += length_

                    # Count the number of reads aligned uniquely with 
                    # some constraints
                    num_reads += 1

                    # Add count statistics to given gene information
                    def add_stat(Gene_cmpt, 
                                 Gene_counts, 
                                 Gene_count_per_read, 
                                 include_alleles = set()):
                        if len(Gene_count_per_read) <= 0:
                            return ""
                        max_count = max(Gene_count_per_read.values())
                        cur_cmpt = set()
                        for allele, count in Gene_count_per_read.items():
                            if count < max_count:
                                continue
                            if len(include_alleles) > 0 \
                                    and allele not in include_alleles:
                                continue
                            
                            cur_cmpt.add(allele)                    
                            if allele not in Gene_counts:
                                Gene_counts[allele] = 1
                            else:
                                Gene_counts[allele] += 1

                        if len(cur_cmpt) == 0:
                            return ""

                        if verbose >= 2:
                            alleles = ["", ""]
                            allele1_found = False
                            allele2_found = False
                            if alleles[0] != "":
                                for allele, count in Gene_count_per_read.items():
                                    if count < max_count:
                                        continue
                                    if allele == alleles[0]:
                                        allele1_found = True
                                    elif allele == alleles[1]:
                                        allele2_found = True
                                if allele1_found != allele2_found:
                                    print((alleles[0], 
                                           Gene_count_per_read[alleles[0]]), 
                                          file=sys.stderr)
                                    print((alleles[1], 
                                           Gene_count_per_read[alleles[1]]), 
                                          file=sys.stderr)
                                    if allele1_found:
                                        print("%s\tread_id %s - %d vs. %d]" 
                                               % (alleles[0], 
                                                  prev_read_id, 
                                                  max_count, 
                                                  Gene_count_per_read[alleles[1]]), 
                                              file=sys.stderr)
                                    else:
                                        print("%s\tread_id %s - %d vs. %d]" 
                                               % (alleles[1], 
                                                  prev_read_id, 
                                                  max_count, 
                                                  Gene_count_per_read[alleles[0]]), 
                                              file=sys.stderr)

                        cur_cmpt = sorted(list(cur_cmpt))
                        cur_cmpt = '-'.join(cur_cmpt)
                        if not cur_cmpt in Gene_cmpt:
                            Gene_cmpt[cur_cmpt] = 1
                        else:
                            Gene_cmpt[cur_cmpt] += 1

                        return cur_cmpt

                    if read_id != prev_read_id:
                        if prev_read_id != None:
                            num_pairs += 1
                            # DK - needs more test
                            #      Several alleles go over 100 bps
                            """
                            if base_fname == "codis" and gene == "D18S51":
                                left_positive_hts, right_positive_hts \
                                    = choose_pairs(left_positive_hts, 
                                                   right_positive_hts)
                            """

                            for positive_ht \
                                    in left_positive_hts | right_positive_hts:
                                
                                primary_exon_hts \
                                    = get_exon_haplotypes(positive_ht, 
                                                          ref_primary_exons)
                                for exon_ht in primary_exon_hts:
                                    add_count(Gene_primary_exons_count_per_read, 
                                              exon_ht, 
                                              1)
                                
                                exon_hts = get_exon_haplotypes(positive_ht, 
                                                               ref_exons)
                                for exon_ht in exon_hts:
                                    add_count(Gene_exons_count_per_read, 
                                              exon_ht, 
                                              1)
                                
                                add_count(Gene_count_per_read, 
                                          positive_ht, 
                                          1)                     

                            cur_cmpt     = ""
                            cur_cmpt_gen = ""
                            if base_fname == "hla":
                                cur_primary_exons_cmpt \
                                    = add_stat(Gene_primary_exons_cmpt, 
                                               Gene_primary_exons_counts, 
                                               Gene_primary_exons_count_per_read,
                                               primary_exon_allele_rep_set)
            
                                cur_exons_cmpt = add_stat(Gene_exons_cmpt, 
                                                          Gene_exons_counts, 
                                                          Gene_exons_count_per_read, 
                                                          allele_rep_set)
                                cur_cmpt = add_stat(Gene_cmpt, 
                                                    Gene_counts, 
                                                    Gene_count_per_read)
                            else:
                                cur_cmpt = add_stat(Gene_cmpt, 
                                                    Gene_counts, 
                                                    Gene_count_per_read)
                            for read_id_, read_id_i, read_node in read_nodes:
                                asm_graph.add_node(read_id_,
                                                   read_id_i,
                                                   read_node,
                                                   simulation)
                            read_nodes    = []
                            read_var_list = []
                            if simulation \
                                    and verbose >= 2 \
                                    and base_fname in ["hla", "codis"]:
                                if cur_cmpt != "":
                                    cur_cmpt = cur_cmpt.split('-') 
                                else:
                                    cur_cmpt = set()

                                if cur_cmpt_gen != "":
                                    cur_cmpt_gen = cur_cmpt_gen.split('-')
                                else:
                                    cur_cmpt_gen = set()
                                
                                # will show debug if needed
                                debug_print = False
                                if partial:
                                    if not set(cur_cmpt) & set(test_Gene_names):
                                        if cur_cmpt != "":
                                            debug_print = True
                                            debug_line = cur_cmpt
                                else:
                                    if not set(cur_cmpt_gen) & set(test_Gene_names):
                                        if cur_cmpt_gen != "":
                                            debug_print = True
                                            debug_line = cur_cmpt_gen                           

                                if debug_print:
                                    print("%s are chosen instead of %s" 
                                          % (debug_line, '-'.join(test_Gene_names)))
                                    for prev_line in prev_lines:
                                        print("\t", prev_line)

                            prev_lines = []

                        left_positive_hts  = set()
                        right_positive_hts = set()                  
                        Gene_primary_exons_count_per_read = {}
                        Gene_exons_count_per_read         = {}
                        Gene_count_per_read               = {}
                        for allele in Gene_names[gene]:
                            if allele.find("BACKBONE") != -1:
                                continue
                            if base_fname == "genome" and allele.find("GRCh38") != -1:
                                continue
                            if allele in primary_exon_allele_rep_set:
                                Gene_primary_exons_count_per_read[allele] = 0
                            if allele in allele_rep_set:
                                Gene_exons_count_per_read[allele] = 0
                            Gene_count_per_read[allele] = 0

                    prev_lines.append(line)

                    # Remove mismatches due to unknown or novel variants
                    cmp_list2 = []
                    for cmp in cmp_list:
                        cmp = deepcopy(cmp)
                        type, pos, length = cmp[:3]
                        if type == "match":
                            if len(cmp_list2) > 0 and cmp_list2[-1][0] == "match":
                                cmp_list2[-1][2] += length
                            else:
                                cmp_list2.append(cmp)
                        elif type == "mismatch" and \
                             (cmp[3] == "unknown" or cmp[3].startswith("nv")):
                            if len(cmp_list2) > 0 and cmp_list2[-1][0] == "match":
                                cmp_list2[-1][2] += 1
                            else:
                                cmp_list2.append(["match", pos, 1])
                        else:
                            cmp_list2.append(cmp)
                    
                    debug_iad = orig_read_id.startswith(
                        "HSQ1009:126:D0UUYACXX:4:2212:9787:80992#")
                    cmp_list_left, \
                      cmp_list_right, \
                      cmp_left_alts, \
                      cmp_right_alts \
                        = typing_common.identify_ambigious_diffs(ref_seq,
                                                                gene_vars,
                                                                Alts_left,
                                                                Alts_right,
                                                                Alts_left_list,
                                                                Alts_right_list,
                                                                cmp_list2,
                                                                verbose,
                                                                debug_iad)

                    mid_ht = []
                    for cmp in cmp_list2[cmp_list_left:cmp_list_right+1]:
                        type = cmp[0]
                        if type not in ["mismatch", "deletion", "insertion"]:
                            continue                            
                        var_id = cmp[3]
                        mid_ht.append(var_id)

                    for l in range(len(cmp_left_alts)):
                        left_ht = cmp_left_alts[l].split('-')
                        left_ht += mid_ht
                        for r in range(len(cmp_right_alts)):
                            right_ht = cmp_right_alts[r].split('-')
                            ht = left_ht + right_ht
                            if len(ht) <= 0:
                                continue
                            ht_str = '-'.join(ht)
                            if is_left_read:
                                left_positive_hts.add(ht_str)
                            else:
                                right_positive_hts.add(ht_str)

                    if assembly:
                        # Construct multiple candidate realignments for CODIS
                        cmp_llist = []
                        if is_left_read:
                            hts = left_positive_hts
                        else:
                            hts = right_positive_hts
                        assert len(hts) > 0
                        for ht in hts:
                            cmp_list = []
                            read_pos = 0
                            vars_    = ht.split('-')
                            left_    = int(vars_[0])
                            vars_    = vars_[1:]
                            for var_i in range(len(vars_)):
                                var_id = vars_[var_i]
                                # ref_seq, read_seq
                                if var_i == len(vars_) - 1:
                                    right_ = int(var_id)
                                else:
                                    var_type, var_pos, var_data = gene_vars[var_id]
                                    right_ = var_pos - 1
                                
                                for pos in range(left_, right_ + 1):
                                    if read_seq[read_pos] != ref_seq[pos]:
                                        if left_ < pos:
                                            cmp_list.append(["match", 
                                                             left_, 
                                                             pos - left_])
                                        cmp_list.append(["mismatch", 
                                                         pos, 
                                                         1, 
                                                         "unknown"])
                                        left_ = pos + 1
                                    read_pos += 1                                    
                                if left_ <= right_:
                                    cmp_list.append(["match", 
                                                     left_, 
                                                     right_ - left_ + 1])
                                    
                                if var_i == len(vars_) - 1:
                                    left_ = right_ + 1
                                    break

                                if var_type == "single":
                                    cmp_list.append(["mismatch", 
                                                     var_pos, 
                                                     1, 
                                                     var_id])
                                    left_ = var_pos + 1
                                    read_pos += 1
                                elif var_type == "deletion":
                                    del_len = int(var_data)
                                    cmp_list.append(["deletion", 
                                                     var_pos, 
                                                     del_len, 
                                                     var_id])
                                    left_ = var_pos + del_len                                    
                                else:
                                    assert var_type == "insertion"
                                    cmp_list.append(["insertion", 
                                                     var_pos, 
                                                     len(var_data), 
                                                     var_id])
                                    left_ = var_pos
                                    read_pos += len(var_data)

                            assert len(cmp_list) > 0
                            cmp_llist.append(cmp_list)

                        for cmp_list_i in range(len(cmp_llist)):
                            # Node and position sets
                            cmp_list = cmp_llist[cmp_list_i]
                            ref_pos         = cmp_list[0][1]
                            read_pos        = 0  
                            cmp_i           = 0                                                      
                            read_node_pos   = -1
                            read_node_seq   = []
                            read_node_qual  = []
                            read_node_var   = []

                            while cmp_i < len(cmp_list):
                                cmp = cmp_list[cmp_i]
                                type, length = cmp[0], cmp[2]
                                if type in ["match", "mismatch"]:
                                    if read_node_pos < 0:
                                        read_node_pos = ref_pos
                                if type == "match":
                                    read_end        = read_pos + length
                                    read_node_seq  += list(read_seq[read_pos:read_end])
                                    read_node_qual += list(read_qual[read_pos:read_end])
                                    read_node_var  += ([''] * length)
                                    read_pos        = read_end
                                elif type == "mismatch":
                                    var_id          = cmp[3]
                                    read_base       = read_seq[read_pos]
                                    qual            = read_qual[read_pos]
                                    read_node_seq  += [read_base]
                                    read_node_qual += [qual]
                                    read_node_var  += [var_id]
                                    read_pos       += 1
                                elif type == "deletion":
                                    var_id          = cmp[3]
                                    del_len         = length
                                    read_node_seq  += (['D'] * del_len)
                                    read_node_qual += ([''] * del_len)
                                    if len(read_node_seq) > len(read_node_var):
                                        assert len(read_node_seq) \
                                            == len(read_node_var) + del_len
                                        read_node_var += ([var_id] * del_len)
                                elif type == "insertion":
                                    var_id          = cmp[3]
                                    ins_len         = length
                                    ins_seq         = read_seq[read_pos:read_end]
                                    read_node_seq  += ["I%s" % nt for nt in ins_seq]
                                    read_node_qual += list(read_qual[read_pos:read_end])
                                    read_node_var  += ([var_id] * ins_len)                                        
                                    read_pos       += read_end
                                else:
                                    assert type == "intron"
                                cmp_i += 1

                            read_nodes.append([node_read_id,
                                               cmp_list_i,
                                               assembly_graph.Node(node_read_id,
                                                                   read_node_pos,
                                                                   read_node_seq,
                                                                   read_node_qual,
                                                                   read_node_var,
                                                                   ref_seq,
                                                                   gene_vars,
                                                                   mpileup,
                                                                   simulation)])

                    prev_read_id   = read_id
                    prev_right_pos = right_pos
 
                if prev_read_id != None:
                    num_pairs += 1
                    if base_fname == "codis" and gene == "D18S51":
                        # Updating Pairs of haplotypes
                        left_positive_hts, \
                          right_positive_hts \
                            = choose_pairs(left_positive_hts, 
                                           right_positive_hts)                            
                    for positive_ht in left_positive_hts | right_positive_hts:
                        primary_exon_hts = get_exon_haplotypes(positive_ht, 
                                                               ref_primary_exons)
                        for exon_ht in primary_exon_hts:
                            add_count(Gene_primary_exons_count_per_read, 
                                      exon_ht, 
                                      1)
                        exon_hts = get_exon_haplotypes(positive_ht, ref_exons)
                        for exon_ht in exon_hts:
                            add_count(Gene_exons_count_per_read, 
                                      exon_ht, 
                                      1)
                        add_count(Gene_count_per_read, 
                                  positive_ht, 
                                  1)

                    if base_fname == "hla":
                        add_stat(Gene_primary_exons_cmpt, 
                                 Gene_primary_exons_counts, 
                                 Gene_primary_exons_count_per_read, 
                                 primary_exon_allele_rep_set)
                        add_stat(Gene_exons_cmpt, 
                                 Gene_exons_counts, 
                                 Gene_exons_count_per_read, 
                                 allele_rep_set)
                    add_stat(Gene_cmpt, 
                             Gene_counts, 
                             Gene_count_per_read)
                    for read_id_, read_id_i, read_node in read_nodes:
                        asm_graph.add_node(read_id_,
                                           read_id_i,
                                           read_node,
                                           simulation)
                    read_nodes    = []
                    read_var_list = []

                if num_reads <= 0:
                    continue

                for f_ in msg_out:
                    print("\t\t\t%d reads and %d pairs are aligned" 
                           % (num_reads, num_pairs), 
                          file=f_)
                
            else:
                assert index_type == "linear"
                def add_alleles(alleles):
                    if not allele in Gene_counts:
                        Gene_counts[allele] = 1
                    else:
                        Gene_counts[allele] += 1

                    cur_cmpt = sorted(list(alleles))
                    cur_cmpt = '-'.join(cur_cmpt)
                    if not cur_cmpt in Gene_cmpt:
                        Gene_cmpt[cur_cmpt] = 1
                    else:
                        Gene_cmpt[cur_cmpt] += 1

                prev_read_id = None
                prev_AS      = None
                alleles      = set()
                for line in alignview_proc_str:
                    if not complete["Align Return"]: # Confirm alingment return
                        complete["Align Return"] = True
                    cols = line[:-1].split()
                    read_id, flag, allele = cols[:3]
                    flag = int(flag)
                    if flag & 0x4 != 0:
                        continue
                    if not allele.startswith(gene):
                        continue
                    if allele.find("BACKBONE") != -1:
                        continue

                    AS = None
                    for i in range(11, len(cols)):
                        col = cols[i]
                        if col.startswith("AS"):
                            AS = int(col[5:])
                    assert AS != None
                    if read_id != prev_read_id:
                        if alleles:
                            if aligner == "hisat2" \
                                    or (aligner == "bowtie2" and len(alleles) < 10):
                                add_alleles(alleles)
                            alleles = set()
                        prev_AS = None
                    if prev_AS != None and AS < prev_AS:
                        continue
                    prev_read_id = read_id
                    prev_AS = AS
                    alleles.add(allele)

                if alleles:
                    add_alleles(alleles)

            Gene_counts = [[allele, count] for allele, count in Gene_counts.items()]
            Gene_counts = sorted(Gene_counts, key = lambda x: x[1], reverse = True)
            for count_i in range(len(Gene_counts)):
                count = Gene_counts[count_i]
                if simulation:
                    found = False
                    for test_Gene_name in test_Gene_names:
                        if count[0] == test_Gene_name:
                            for f_ in msg_out:
                                print("\t\t\t*** %d ranked %s (count: %d)" 
                                       % (count_i + 1, test_Gene_name, count[1]), 
                                      file=f_)
                            found = True
                    if count_i < 5 and not found:
                        for f_ in msg_out:
                            print("\t\t\t\t%d %s (count: %d)" 
                                   % (count_i + 1, count[0], count[1]), 
                                  file=f_)
                else:
                    for f_ in msg_out:
                        print("\t\t\t\t%d %s (count: %d)" 
                               % (count_i + 1, count[0], count[1]), 
                              file=f_)
                    if count_i >= 9 and not output_allele_counts:
                        break
            for f_ in msg_out:
                print("\n", 
                      file=f_)

            # Calculate the abundance of representative alleles 
            # on exonic sequences
            if base_fname == "hla":
                perform_typing_primary_exon = False
                # Incorporate representive alleles for primary exons 
                # (experimental feature)
                if perform_typing_primary_exon:
                    Gene_prob \
                        = primary_exon_prob \
                            = typing_common.single_abundance(Gene_primary_exons_cmpt)
                    primary_exon_alleles = set()
                    primary_exon_prob_sum = 0.0
                    for prob_i in range(len(primary_exon_prob)):
                        allele, prob = primary_exon_prob[prob_i][:2]
                        primary_allele_group = primary_exon_allele_rep_groups[allele]
                        if len(primary_allele_group) <= 1:
                            continue
                        primary_exon_prob_sum += prob
                        primary_exon_alleles |= set(primary_allele_group)

                    # Incorporate representative alleles for exons
                    if len(primary_exon_alleles) > 0:
                        Gene_exons_cmpt2 = {}
                        for cmpt, value in Gene_exons_cmpt.items():
                            cmpt2 = []
                            for allele in cmpt.split('-'):
                                if allele in primary_exon_alleles:
                                    cmpt2.append(allele)
                            if len(cmpt2) == 0:
                                continue
                            cmpt2 = '-'.join(cmpt2)
                            if cmpt2 not in Gene_exons_cmpt2:
                                Gene_exons_cmpt2[cmpt2] = value
                            else:
                                Gene_exons_cmpt2[cmpt2] += value
                        exon_prob = typing_common.single_abundance(
                            Gene_exons_cmpt2,
                            remove_low_abundance_alleles
                        )
                        exon_prob2 = {}
                        for allele, prob in primary_exon_prob:
                            if allele not in primary_exon_alleles:
                                exon_prob2[allele] = prob
                        for allele, prob in exon_prob:
                            exon_prob2[allele] = prob * primary_exon_prob_sum
                        exon_prob = list(
                            [allele, prob] for allele, prob in exon_prob2.items()
                        )
                        Gene_prob \
                            = exon_prob \
                                = sorted(exon_prob, key = lambda x: x[1], reverse=True)
                else:
                    # Incorporate representative alleles for exons
                    Gene_prob \
                        = exon_prob \
                            = typing_common.single_abundance(
                                Gene_exons_cmpt,
                                remove_low_abundance_alleles
                            )

                exon_alleles = set()
                exon_prob_sum = 0.0
                for prob_i in range(len(exon_prob)):
                    allele, prob = exon_prob[prob_i][:2]
                    if prob_i >= 10 and prob < 0.03:
                        break
                    if len(allele_rep_groups[allele]) <= 1:
                        continue

                    exon_prob_sum += prob
                    exon_alleles |= set(allele_rep_groups[allele])

                # Incorporate full-length alleles, non-representative alleles
                if len(exon_alleles) > 0:
                    Gene_cmpt2 = {}
                    for cmpt, value in Gene_cmpt.items():
                        cmpt2 = []
                        for allele in cmpt.split('-'):
                            if allele in exon_alleles:
                                cmpt2.append(allele)
                        if len(cmpt2) == 0:
                            continue
                        cmpt2 = '-'.join(cmpt2)
                        if cmpt2 not in Gene_cmpt2:
                            Gene_cmpt2[cmpt2] = value
                        else:
                            Gene_cmpt2[cmpt2] += value
                    Gene_cmpt = Gene_cmpt2
                    Gene_prob = typing_common.single_abundance(Gene_cmpt,
                                                               True,
                                                               Gene_lengths[gene])

                    Gene_combined_prob = {}
                    for allele, prob in exon_prob:
                        if allele not in exon_alleles:
                            Gene_combined_prob[allele] = prob

                    for allele, prob in Gene_prob:
                        Gene_combined_prob[allele] = prob * exon_prob_sum
                                            
                    Gene_prob = list(
                        [allele, prob] for allele, prob in Gene_combined_prob.items()
                    )
                    Gene_prob = sorted(Gene_prob, key = lambda x: x[1], reverse=True)
            else:
                if len(Gene_cmpt.keys()) <= 1:
                    Gene_prob = []
                    if len(Gene_cmpt.keys()) == 1:
                        Gene_prob = [[Gene_cmpt.keys()[0], 1.0]]
                else:
                    Gene_prob = typing_common.single_abundance(Gene_cmpt)

            if index_type == "graph" and assembly:
                allele_node_order = []
                predicted_allele_nodes = {}
                for allele_name, prob in Gene_prob:
                    if prob < 0.1: # abundance of 10%
                        break
                    predicted_allele_nodes[allele_name] \
                        = create_allele_node(allele_name)
                    allele_node_order.append([allele_name, prob])
                    if len(predicted_allele_nodes) >= 2:
                        break
                asm_graph.predicted_allele_nodes = predicted_allele_nodes
                asm_graph.allele_node_order      = allele_node_order
                asm_graph.calculate_coverage()
                
                # Start drawing assembly graph
                fname = "%s.%s" % (report_base, gene)
                asm_graph.begin_draw(fname)

                # Draw assembly graph
                try:
                    begin_y = asm_graph.draw(0, "a. Read alignment")
                    begin_y += 200
                
                    # Apply De Bruijn graph
                    viterbi_calls[gene] = asm_graph.guided_DeBruijn(assembly_verbose)

                    # Draw assembly graph
                    begin_y = asm_graph.draw(begin_y, "b. Assembly")
                    begin_y += 200

                    # Draw assembly graph
                    asm_graph.nodes = asm_graph.nodes2
                    asm_graph.to_node, asm_graph.from_node = {}, {}
                    begin_y = asm_graph.draw(begin_y, "c. Assembly with known alleles")

                    # End drawing assembly graph
                    asm_graph.end_draw()
                
                except Exception as err:
                    assembly = False
                    for f_ in msg_out:
                        print("Error in building and calling viterbi", 
                              file=f_)
                    if SANITY_CHECK:
                        print(err, file = sys.stderr)
                        print("Error on line {}".format(sys.exc_info()[-1].tb_lineno))
                        raise

                # Compare two alleles
                if simulation and len(test_Gene_names) == 2:
                    allele_name1, allele_name2 = test_Gene_names
                    print(allele_name1, "vs.", allele_name2, 
                        file=sys.stderr)
                    asm_graph.print_node_comparison(asm_graph.true_allele_nodes)

                def compare_alleles(vars1, 
                                    vars2, 
                                    print_output = True):
                    skip       = True
                    var_i      = 0
                    var_j      = 0
                    exon_i     = 0
                    mismatches = 0
                    allele_seq = list(ref_seq)
                    while var_i < len(vars1) and var_j < len(vars2):
                        cmp_var_id  = vars1[var_i]
                        node_var_id = vars2[var_j]
                        cmp_var     = gene_vars[cmp_var_id]
                        node_var    = gene_vars[node_var_id]
                        min_pos     = min(cmp_var[1], node_var[1])

                        cmp_var_in_exon  = False
                        node_var_in_exon = False
                        while exon_i < len(ref_exons):
                            exon_left, exon_right = ref_exons[exon_i]
                            if min_pos <= exon_right:
                                if cmp_var[1] >= exon_left \
                                        and cmp_var[1] <= exon_right:
                                    cmp_var_in_exon = True
                                else:
                                    cmp_var_in_exon = False
                                if node_var[1] >= exon_left \
                                        and node_var[1] <= exon_right:
                                    node_var_in_exon = True
                                else:
                                    node_var_in_exon = False                                
                                break
                            exon_i += 1
                        
                        if cmp_var_id == node_var_id:
                            skip = False
                            if print_output:
                                for f_ in msg_out:
                                    if cmp_var_in_exon:
                                        print("\033[94mexon%d\033[00m" % (exon_i + 1), 
                                            file=f_)
                                    print(cmp_var_id, 
                                        cmp_var, 
                                        "\t\t\t", mpileup[cmp_var[1]], 
                                        file=f_)
                            var_i += 1
                            var_j += 1

                            var_type, var_pos, var_data = cmp_var
                            if var_type == "single":
                                allele_seq[var_pos] = var_data
                            elif var_type == "deletion":
                                var_data = int(var_data)
                                allele_seq[var_pos:var_pos+var_data] = '.' * var_data
                            else:
                                assert var_type == "insertion"
                            continue
                        if cmp_var[1] <= node_var[1]:
                            if not skip:
                                if (var_i > 0 and var_i + 1 < len(vars1)) \
                                        or cmp_var[0] != "deletion":
                                    if print_output:
                                        if cmp_var_in_exon:
                                            for f_ in msg_out:
                                                print("\033[94mexon%d\033[00m" 
                                                       % (exon_i + 1), 
                                                      file=f_)
                                        for f_ in msg_out:
                                            print("***", 
                                                    cmp_var_id, 
                                                    cmp_var, 
                                                    "==", 
                                                    "\t\t\t", 
                                                    mpileup[cmp_var[1]], 
                                                  file=sys.stderr)
                                    mismatches += 1
                            var_i += 1
                        else:
                            if print_output:
                                if node_var_in_exon:
                                    for f_ in msg_out:
                                        print("\033[94mexon%d\033[00m" 
                                               % (exon_i + 1), 
                                              file=f_)
                                for f_ in msg_out:
                                    print("*** ==", 
                                            node_var_id, 
                                            node_var, 
                                            "\t\t\t", 
                                            mpileup[node_var[1]], 
                                          file=f_)
                            mismatches += 1
                            var_j += 1

                    allele_exons = ref_exons[:]
                    allele_seq = ''.join(allele_seq)
                    del_counts = []
                    for del_i in range(len(allele_seq)):
                        del_count = 0 if del_i == 0 else del_counts[-1]
                        if allele_seq[del_i] == '.':
                            del_count += 1
                        del_counts.append(del_count)
                    for exon_i in range(len(allele_exons)):
                        exon_left, exon_right = allele_exons[exon_i]
                        exon_left            -= del_counts[exon_left]
                        exon_right           -= del_counts[exon_right]
                        allele_exons[exon_i]  = [exon_left, exon_right]
                        
                    allele_seq = allele_seq.replace('.', '')
                    return allele_seq, allele_exons, mismatches
                    
                tmp_nodes = asm_graph.nodes
                for f_ in msg_out:
                    print("Number of tmp nodes:", len(tmp_nodes), 
                        file=f_)
                count = 0
                for id, node in tmp_nodes.items():
                    count += 1
                    if count > 10:
                        break
                    node_vars = node.get_var_ids()
                    for f_ in msg_out:
                        node.print_info(f_)
                        print("\n", file=f_)
                        if node.id in asm_graph.to_node:
                            for id2, at in asm_graph.to_node[node.id]:
                                print("\tat %d ==> %s" % (at, id2), 
                                    file=f_)

                    if simulation:
                        cmp_Gene_names = test_Gene_names
                    else:
                        # List of allele names
                        cmp_Gene_names = [aname for aname, _ in allele_node_order]
                        
                    alleles, cmp_vars, max_common = [], [], -sys.maxsize
                    for cmp_Gene_name in cmp_Gene_names:
                        tmp_vars \
                            = allele_nodes[cmp_Gene_name].get_var_ids(node.left, 
                                                                      node.right)
                        
                        tmp_common  = len(set(node_vars) & set(tmp_vars))
                        tmp_common -= len(set(node_vars) | set(tmp_vars))
                        if max_common < tmp_common:
                            max_common = tmp_common
                            alleles = [[cmp_Gene_name, tmp_vars]]
                        elif max_common == tmp_common:
                            alleles.append([cmp_Gene_name, tmp_vars])

                    for allele_name, cmp_vars in alleles:
                        allele_seq, \
                            allele_exons, \
                            allele_mm \
                            = compare_alleles(cmp_vars, node_vars)

                        for f_ in msg_out:
                            print("vs.", allele_name, 
                                  file=f_)
                            print("\t\tallele sequence (%d bps):" % len(allele_seq), 
                                  allele_seq, 
                                  file=f_)
                            print("\t\texons (zero-based offset):", allele_exons, 
                                  file=f_)

                    for f_ in msg_out:
                        print("\n\n", file=f_)

            # Identify alleles that perfectly or closesly match assembled alleles
            fasta_dic  = {}
            contig_cnt = 0
            for node_name, node in asm_graph.nodes.items():
                vars = set(node.get_var_ids())

                fasta_key        = node_name + " " + "contig %d" % contig_cnt + " "
                max_allele_names = []
                max_common       = -sys.maxsize
                for allele_name, vars2 in allele_vars.items():
                    vars2 = set(vars2)
                    tmp_common = len(vars & vars2) - len(vars | vars2)
                    if tmp_common > max_common:
                        max_common       = tmp_common
                        max_allele_names = [allele_name]                        
                    elif tmp_common == max_common:
                        max_allele_names.append(allele_name)

                for f_ in msg_out:
                    print("\tGenomic:", node_name, file=f_)
                    
                node_vars      = node.get_var_ids()
                min_mismatches = sys.maxsize
                node_call      = ""
                for max_allele_name in max_allele_names:
                    cmp_vars = allele_vars[max_allele_name]
                    cmp_vars.sort(key=lambda x: int(x[2:]))
                    
                    _, _, tmp_mismatches = compare_alleles(cmp_vars, 
                                                            node_vars, 
                                                            print_output = False)
                    for f_ in msg_out:
                        print("\t\t%s:" % max_allele_name, 
                              max_common, 
                              tmp_mismatches,
                              file=f_)

                    if tmp_mismatches < min_mismatches:
                        min_mismatches = tmp_mismatches
                        node_call      = max_allele_name
                    
                if min_mismatches > 0:
                    fasta_key += "Novel"
                    for f_ in msg_out:
                        print("\tNovel allele", file=f_)
                    
                else:
                    fasta_key += node_call
                    for f_ in msg_out:
                        print("\tKnown allele", file=f_)

                if SANITY_CHECK:
                    print(fasta_key, file=sys.stderr)
                    print(node.get_seq(), file=sys.stderr)

                fasta_dic[fasta_key] = node.get_seq()
                contig_cnt += 1    

            if fasta_dic:
                typing_common.write_fasta(report_base + '.fasta',
                                          fasta_dic)

            if simulation:
                success = [False for i in range(len(test_Gene_names))]
                found_list = [False for i in range(len(test_Gene_names))]
            for prob_i in range(len(Gene_prob)):
                prob = Gene_prob[prob_i]
                if prob[1] < 0.01:
                    break
                found = False
                _allele_rep = prob[0]

                if simulation:
                    for name_i in range(len(test_Gene_names)):
                        test_Gene_name = test_Gene_names[name_i]
                        if prob[0] == test_Gene_name:
                            rank_i = prob_i
                            while rank_i > 0:
                                if prob == Gene_prob[rank_i - 1][1]:
                                    rank_i -= 1
                                else:
                                    break
                            for f_ in msg_out:
                                print("\t\t\t*** %d ranked %s (abundance: %.2f%%)" 
                                        % (rank_i+1, test_Gene_name, prob[1]*100.0), 
                                      file=f_)
                            if rank_i < len(success):
                                success[rank_i] = True
                            found_list[name_i] = True
                            found = True
                    # DK - for debugging purposes
                    if not False in found_list and prob_i >= 10:
                        break
                if not found:
                    for f_ in msg_out:
                        print("\t\t\t\t%d ranked %s (abundance: %.2f%%)" 
                                % (prob_i + 1, _allele_rep, prob[1] * 100.0), 
                               file=f_)

                        if best_alleles and prob_i < 2:
                            print("SingleModel %s (abundance: %.2f%%)" 
                                   % (_allele_rep, prob[1] * 100.0), 
                                  file=f_)

                if not simulation and prob_i >= 9:
                    break
                if prob_i >= 19:
                    break
            print("\n", file=sys.stderr)

            # TODO - CB I can switch between full and partial success counting
            # I need to decide how to handle this block of code
            # if simulation and not False in success:
            #     aligner_type = "%s %s" % (aligner, index_type)
            #     if not aligner_type in test_passed:
            #         test_passed[aligner_type] = 1
            #     else:
            #         test_passed[aligner_type] += 1

            if simulation:
                for iscorrect in success:
                    if not iscorrect:
                        continue

                    aligner_type = "%s %s" % (aligner, index_type)
                    if not aligner_type in test_passed:
                        test_passed[aligner_type] = 1
                    else:
                        test_passed[aligner_type] += 1

        if not keep_alignment and remove_alignment_file:
            os.system("rm %s*" % (alignment_fname))

    if assembly:
        for f_ in msg_out:
            print("\t\tAssembly Coloring Allele Collapse:", 
                  file=f_)
            for genename, calls in viterbi_calls.items():
                if calls:
                    print("\t\t\t%s: %s (Group score: %.5f)" 
                           % (genename, ' : '.join(calls[0]), 10**calls[1]), 
                          file=f_)
                else:
                    print("\t\t\t%s: NONE (Group score: NA)" 
                           % (genename), 
                          file=f_)

    report_file.close()

    # Check if all runs occured properly
    for key, value in complete.items():
        if not value:
            print("Error in running HISATgenotype: Incomplete %s" % key,
                  file=sys.stderr)
            exit(1)

    if simulation:
        return test_passed

""" Extract backbone allele sequence that is imbedded in the genotype_genome """
""" Will load the sequences into the Genes dictionary """
def read_backbone_alleles(genotype_genome, refGene_loci, Genes):
    for gene_name in refGene_loci:
        allele_name, chr, left, right = refGene_loci[gene_name][:4]
        genome_loci = "%s:%d-%d" % (chr, left+1, right+1)
        seq_extract_cmd = ["samtools", "faidx", "%s.fa" % genotype_genome, genome_loci]

        length = right - left + 1
        proc = subprocess.Popen(seq_extract_cmd, 
                                universal_newlines = True,
                                stdout = subprocess.PIPE, 
                                stderr = open("/dev/null", 'w'))
        seq = ""
        for line in proc.stdout:
            line = line.strip()
            if line.startswith('>'):
                continue
            seq += line
        assert len(seq) == length
        assert gene_name not in Genes
        Genes[gene_name] = {}
        Genes[gene_name][allele_name] = seq

""" Building allele sequences from Variants and backbone sequence """
""" Similar to how AEGIS builds alleles """
def read_Gene_alleles_from_vars(Vars, Var_list, Links, Genes):
    for gene_name in Genes:
        # Assert there is only one allele per gene, which is a backbone allele
        assert len(Genes[gene_name]) == 1
        backbone_allele_name, backbone_seq = list(Genes[gene_name].items())[0]
        gene_vars     = Vars[gene_name]
        gene_var_list = Var_list[gene_name]
        allele_vars   = {}
        for _, var_id in gene_var_list:
            if var_id not in Links:
                continue
            for allele_name in Links[var_id]:
                if allele_name not in allele_vars:
                    allele_vars[allele_name] = []
                allele_vars[allele_name].append(var_id)

        for allele_name, vars in allele_vars.items():
            seq = ""
            prev_pos = 0
            for var_id in vars:
                type, pos, data = gene_vars[var_id]
                assert prev_pos <= pos
                if pos > prev_pos:
                    seq += backbone_seq[prev_pos:pos]
                if type == "single":
                    prev_pos = pos + 1
                    seq += data
                elif type == "deletion":
                    prev_pos = pos + int(data)
                else:
                    assert type == "insertion"
                    seq += data
                    prev_pos = pos
            if prev_pos < len(backbone_seq):
                seq += backbone_seq[prev_pos:]
            Genes[gene_name][allele_name] = seq

        if len(Genes[gene_name]) <= 1:
            Genes[gene_name]["%s*GRCh38" % gene_name] = backbone_seq

""" Derives Vars and Var_list from .snp file and adjusts with genome data """
def read_Gene_vars_genotype_genome(fname, refGene_loci):
    loci = {}
    for gene, values in refGene_loci.items():
        allele_name, chr, left, right = values[:4]
        if chr not in loci:
            loci[chr] = []
        loci[chr].append([allele_name, left, right])
        
    Vars, Var_list = {}, {}
    for line in open(fname):
        var_id, var_type, var_chr, pos, data = line.strip().split('\t')
        if var_chr not in loci:
            continue
        pos = int(pos)
        found = False
        for allele_name, left, right in loci[var_chr]:
            if pos >= left and pos <= right:
                found = True
                break
        if not found:
            continue
        
        gene = allele_name.split('*')[0]
        if not gene in Vars:
            Vars[gene] = {}
            assert not gene in Var_list
            Var_list[gene] = []
            
        assert not var_id in Vars[gene]
        Vars[gene][var_id] = [var_type, pos - left, data]
        Var_list[gene].append([pos - left, var_id])
        
    for gene, in_var_list in Var_list.items():
        Var_list[gene] = sorted(in_var_list)

    return Vars, Var_list

""" Wrapper for sequentially running typing on all loci of interest """
def genotyping_locus(base_fname,
                     locus_list,
                     genotype_genome,
                     ix_dir,
                     only_locus_list,
                     partial,
                     aligners,
                     read_fname,
                     fastq,
                     alignment_fname,
                     threads,
                     simulate_interval,
                     read_len,
                     fragment_len,
                     best_alleles,
                     num_editdist,
                     perbase_errorrate,
                     perbase_snprate,
                     skip_fragment_regions,
                     assembly,
                     output_base,
                     error_correction,
                     keep_alignment,
                     discordant,
                     type_primary_exons,
                     remove_low_abundance_alleles,
                     display_alleles,
                     verbose,
                     assembly_verbose,
                     out_dir,
                     output_allele_counts,
                     debug_instr):
    assert isinstance(base_fname, basestring)
    assert not ',' in base_fname
    assert os.path.exists(ix_dir)

    dbversion       = ''
    refGenes        = {}
    refGene_loci    = {}
    Genes           = {}
    alleles         = set()
    partial_alleles = set()
    simulation      = (read_fname == [] and alignment_fname == "") 
    if genotype_genome:
        full_gg_path = ix_dir + "/" + genotype_genome

        # Check if the pre-existing files (hla*) are compatible with the current
        # parameter setting
        if os.path.exists("%s/%s.locus" % (ix_dir, base_fname)):
            left       = 0
            Gene_genes = []
            BACKBONE   = False
            for line in open("%s/%s.locus" % (ix_dir, base_fname)):
                Gene_name = line.strip().split()[0]
                if Gene_name.find("BACKBONE") != -1:
                    BACKBONE = True
                Gene_gene = Gene_name.split('*')[0]
                Gene_genes.append(Gene_gene)
            delete_hla_files = False
            if not BACKBONE:
                delete_hla_files = True
            if len(locus_list) == 0:
                locus_list = Gene_genes
            if not set(locus_list).issubset(set(Gene_genes)):
                delete_hla_files = True
            if delete_hla_files:
                print("Error: Current %s build does not contain the --locus-list loci"\
                        "Please rebuild the database.",
                      file=sys.stderr)
                exit(1)

        # Extract variants, backbone sequence, and other sequeces  
        genome_fnames = [full_gg_path + ".fa",
                         full_gg_path + ".fa.fai",
                         full_gg_path + ".locus",
                         full_gg_path + ".snp",
                         full_gg_path + ".index.snp",
                         full_gg_path + ".haplotype",
                         full_gg_path + ".link",
                         full_gg_path + ".clnsig",
                         full_gg_path + ".coord",
                         full_gg_path + ".allele",
                         full_gg_path + ".partial"]
        for i in range(8):
            genome_fnames.append(full_gg_path + ".%d.ht2" % (i+1))

        if not typing_common.check_files(genome_fnames):
            print("Error: index files missing", file=sys.stderr)
            sys.exit(1)

        # Read alleles
        for line in open("%s.allele" % full_gg_path):
            family, allele_name = line.strip().split('\t')
            if family == base_fname:
                alleles.add(allele_name)

        # Read partial alleles
        for line in open("%s.partial" % full_gg_path):
            family, allele_name = line.strip().split('\t')
            if family == base_fname:
                partial_alleles.add(allele_name)
        
        # Read alleles (names and sequences)
        typing_common.read_locus("%s.locus" % full_gg_path,
                                 True, # this is the genotype genome
                                 base_fname,
                                 refGenes,
                                 refGene_loci)

        # Read variants, and link information
        Vars, Var_list = read_Gene_vars_genotype_genome("%s.snp" % full_gg_path, refGene_loci)
        Links = typing_common.read_links("%s.link" % full_gg_path)

        # Read allele sequences
        read_backbone_alleles(full_gg_path, refGene_loci, Genes)
        read_Gene_alleles_from_vars(Vars, Var_list, Links, Genes)

    else:
        full_gg_path = ix_dir + "/" + base_fname

        # Download human genome and HISAT2 index
        typing_common.clone_hisatgenotype_database(ix_dir)
        typing_common.download_genome_and_index(ix_dir)  

        typing_common.extract_database_if_not_exists(base_fname,
                                                     only_locus_list,
                                                     ix_dir,
                                                     30,           # inter_gap
                                                     50,           # intra_gap
                                                     partial,
                                                     verbose >= 1)        
        for aligner, index_type in aligners:
            typing_common.build_index_if_not_exists(base_fname,
                                                    ix_dir,
                                                    aligner,
                                                    index_type,
                                                    threads,
                                                    verbose >= 1)
        # Read alleles
        for line in open("%s.allele" % full_gg_path):
            alleles.add(line.strip())
        
        # Read partial alleles
        for line in open("%s.partial" % full_gg_path):
            partial_alleles.add(line.strip())

        # Read alleles (names and sequences)
        if base_fname == "genome": # Reads refGene info from the locus list
            for chr, left, right in locus_list:
                region_name = "%s:%d-%d" % (chr, left, right)
                refGenes[region_name] = region_name
                refGene_loci[region_name] = [region_name, chr, left, right, []]
        else:
            typing_common.read_locus("%s.locus" % full_gg_path,
                                     False, # this is not the genotype genome
                                     base_fname,
                                     refGenes,
                                     refGene_loci)

        # Read variants, and link information
        Vars, Var_list = typing_common.read_variants("%s.snp" % full_gg_path, True)
        Links = typing_common.read_links("%s.link" % full_gg_path)

        # Read allele sequences
        typing_common.read_allele_seq(full_gg_path + "_backbone.fa", Genes, True)
        read_Gene_alleles_from_vars(Vars, Var_list, Links, Genes)

    # Get database version if exsists
    if os.path.exists(full_gg_path + ".version"):
        dbversion = open(full_gg_path + ".version", 'r').read()
    else:
        dbversion = "NONE"
    
    if len(locus_list) == 0:
        locus_list = refGene_loci.keys()

    # Some loci may have only one allele such as AMELX and AMELY
    for gene_name in refGene_loci.keys():
        if gene_name in Vars:
            continue
        Vars[gene_name]     = {}
        Var_list[gene_name] = []
        Links[gene_name]    = {}        

    # alleles corresponding to backbones
    for allele in alleles:
        locus = allele.split('*')[0]
        assert locus in Genes
        if allele not in Genes[locus]:
            Genes[locus][allele] = Genes[locus]["%s*BACKBONE" % locus]

    # Sanity Check
    if SANITY_CHECK \
            and os.path.exists(full_gg_path + "_backbone.fa") \
            and os.path.exists(full_gg_path + "_sequences.fa"):
        validation_check.check_allele_validity(full_gg_path, Genes)

    # alleles names
    Gene_names = {}
    for Gene_gene, data in Genes.items():
        Gene_names[Gene_gene] = list(data.keys())

    # allele lengths
    Gene_lengths = {}
    for Gene_gene, Gene_alleles in Genes.items():
        Gene_lengths[Gene_gene] = {}
        for allele_name, seq in Gene_alleles.items():
            Gene_lengths[Gene_gene][allele_name] = len(seq)

    # Test typing
    if simulation:
        basic_test  = True
        pair_test   = False
        test_size   = 200
        ranseed     = None
        test_passed = {}
        test_list   = []
        if debug_instr:
            if "pair" in debug_instr:
                basic_test = False
                pair_test  = True
            if "test_size" in debug_instr:
                test_size  = int(debug_instr["test_size"])
            if "set_seed" in debug_instr:
                ranseed    = debug_instr["set_seed"]
            if "test_list" in debug_instr:
                test_list  = [[debug_instr["test_list"].split('-')]]

        if not test_list:
            genes     = list(set(locus_list) & set(Gene_names.keys()))
            test_list = [[] for x in range(test_size)]            
            if basic_test:
                allele_count = 1
            elif pair_test:
                allele_count = 2

            for gene in genes:
                Gene_gene_alleles = deepcopy(Gene_names[gene])
                Gene_gene_alleles.remove(gene + "*BACKBONE")

                random.seed(ranseed)
                arr_loci = random.sample(range(len(Gene_gene_alleles)), 
                                         test_size * allele_count)

                for arr_i in range(0, len(arr_loci), allele_count):
                    test_set = []
                    allele_1 = Gene_gene_alleles[arr_loci[arr_i]]
                    allele_2 = Gene_gene_alleles[arr_loci[arr_i + allele_count - 1]]
                    if basic_test:
                        test_set.append([allele_1])
                    else:
                        assert pair_test
                        test_set.append(sorted([allele_1, allele_2]))
                    test_list[int(arr_i/allele_count)] += test_set

        for test_i in range(len(test_list)):
            if "test_id" in debug_instr:
                test_ids = debug_instr["test_id"].split('-')
                if str(test_i + 1) not in test_ids:
                    continue

            print("Test %d" % (test_i + 1), str(datetime.now()), file=sys.stderr)
            test_locus_list = test_list[test_i]
            num_frag_list = typing_common.simulate_reads(Genes,
                                                         base_fname,
                                                         test_locus_list,
                                                         Vars,
                                                         Links,
                                                         simulate_interval,
                                                         read_len,
                                                         fragment_len,
                                                         perbase_errorrate,
                                                         perbase_snprate,
                                                         skip_fragment_regions,
                                                         out_dir,
                                                         test_i)

            assert len(num_frag_list) == len(test_locus_list)
            for i_ in range(len(test_locus_list)):
                test_Gene_names = test_locus_list[i_]
                num_frag_list_i = num_frag_list[i_]
                assert len(num_frag_list_i) == len(test_Gene_names)
                for j_ in range(len(test_Gene_names)):
                    test_Gene_name = test_Gene_names[j_]
                    gene = test_Gene_name.split('*')[0]
                    test_Gene_seq = Genes[gene][test_Gene_name]
                    if test_Gene_name in partial_alleles:
                        seq_type = "partial" 
                    else: 
                        seq_type = "full"
                    print("\t%s - %d bp (%s sequence, %d pairs)" 
                           % (test_Gene_name, 
                              len(test_Gene_seq), 
                              seq_type, 
                              num_frag_list_i[j_]), 
                          file=sys.stderr)

            if "single-end" in debug_instr:
                read_fname = ["%s_input_1.fa" % base_fname]
            else:
                read_fname = ["%s_input_1.fa" % base_fname, 
                              "%s_input_2.fa" % base_fname]

            fastq = False
            tmp_test_passed = typing(simulation,
                                     full_gg_path,
                                     test_locus_list,
                                     genotype_genome,
                                     partial,
                                     partial_alleles,
                                     refGenes,
                                     Genes,                       
                                     Gene_names,
                                     Gene_lengths,
                                     refGene_loci,
                                     Vars,
                                     Var_list,
                                     Links,
                                     aligners,
                                     num_editdist,
                                     assembly,
                                     output_base,
                                     error_correction,
                                     keep_alignment,
                                     discordant,
                                     type_primary_exons,
                                     remove_low_abundance_alleles,
                                     display_alleles,
                                     fastq,
                                     read_fname,
                                     alignment_fname,
                                     num_frag_list,
                                     read_len,
                                     fragment_len,
                                     threads,
                                     best_alleles,
                                     verbose,
                                     assembly_verbose,
                                     out_dir,
                                     dbversion,
                                     output_allele_counts,
                                     test_i)

            didpass = False
            for aligner_type, passed in tmp_test_passed.items():
                if aligner_type in test_passed:
                    test_passed[aligner_type] += passed
                else:
                    test_passed[aligner_type] = passed

                didpass = True

            if didpass:
                print("\t\tPassed so far: %d/%d (%.2f%%)" 
                       % (test_passed[aligner_type], 
                          ((test_i + 1) * allele_count * len(genes)), 
                          (test_passed[aligner_type] * 100.0 \
                              / ((test_i + 1) * allele_count * len(genes)))), 
                       file=sys.stderr)
            else:
                print("\t\tTest Failed!",
                      file=sys.stderr)


        for aligner_type, passed in test_passed.items():
            print("%s:\t%d/%d passed (%.2f%%)" 
                   % (aligner_type, 
                      passed, 
                      len(test_list) * allele_count * len(genes), 
                      passed * 100.0 / (len(test_list) * allele_count * len(genes))), 
                  file=sys.stderr)
    
    else: # With real reads or BAMs
        if base_fname == "genome":
            print("\t", locus_list, file=sys.stderr)
        else:
            print("\t", ' '.join(locus_list), file=sys.stderr)
        typing(simulation,
               full_gg_path,
               locus_list,
               genotype_genome,
               partial,
               partial_alleles,
               refGenes,
               Genes,                       
               Gene_names,
               Gene_lengths,
               refGene_loci,
               Vars,
               Var_list,
               Links,
               aligners,
               num_editdist,
               assembly,
               output_base,
               error_correction,
               keep_alignment,
               discordant,
               type_primary_exons,
               remove_low_abundance_alleles,
               display_alleles,
               fastq,
               read_fname,
               alignment_fname,
               [],
               read_len,
               fragment_len,
               threads,
               best_alleles,
               verbose,
               assembly_verbose,
               out_dir,
               dbversion,
               output_allele_counts)
