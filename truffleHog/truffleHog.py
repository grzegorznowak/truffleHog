#!/usr/bin/env python
# -*- coding: utf-8 -*-

from __future__ import absolute_import
import shutil
import sys
import math
import datetime
import argparse
import uuid
import hashlib
import tempfile
import os
import re
import json
import stat
from git import Repo
from git import NULL_TREE
from truffleHogRegexes.regexChecks import regexes


def main():
    parser = argparse.ArgumentParser(description='Find secrets hidden in the depths of git.')
    parser.add_argument('--json', dest="output_json", action="store_true", help="Output in JSON")
    parser.add_argument("--regex", dest="do_regex", action="store_true", help="Enable high signal regex checks")
    parser.add_argument("--rules", dest="rules", help="Ignore default regexes and source from json list file")
    parser.add_argument("--entropy", dest="do_entropy", help="Enable entropy checks")
    parser.add_argument("--since_commit", dest="since_commit", help="Only scan from a given commit hash")
    parser.add_argument("--max_depth", dest="max_depth", help="The max commit depth to go back when searching for secrets")
    parser.add_argument("--branch", dest="branch", help="Name of the branch to be scanned")
    parser.add_argument("--repo_path", type=str, dest="repo_path", help="Path to the cloned repo. If provided, git_url will not be used")
    parser.add_argument("--cleanup", dest="cleanup", action="store_true", help="Clean up all temporary result files")
    parser.add_argument("--contextify", dest="contextify", action="store_true", help="Don't display full diffs, only essential context")
    parser.add_argument('git_url', type=str, help='URL for secret searching')
    parser.set_defaults(regex=False)
    parser.set_defaults(rules={})
    parser.set_defaults(max_depth=1000000)
    parser.set_defaults(since_commit=None)
    parser.set_defaults(entropy=True)
    parser.set_defaults(branch=None)
    parser.set_defaults(repo_path=None)
    parser.set_defaults(cleanup=False)
    parser.set_defaults(contextify=False)
    args = parser.parse_args()
    rules = {}
    if args.rules:
        try:
            with open(args.rules, "r") as ruleFile:
                rules = json.loads(ruleFile.read())
                for rule in rules:
                    rules[rule] = re.compile(rules[rule])
        except (IOError, ValueError) as e:
            raise("Error reading rules file")
        for regex in dict(regexes):
            del regexes[regex]
        for regex in rules:
            regexes[regex] = rules[regex]
    do_entropy = str2bool(args.do_entropy)
    output = find_strings(args.git_url, args.since_commit, args.max_depth,
                          args.output_json, args.do_regex,
                          do_entropy, surpress_output=False,
                          branch=args.branch, repo_path=args.repo_path,
                          contextify=args.contextify)
    project_path = output["project_path"]
    shutil.rmtree(project_path, onerror=del_rw)
    if args.cleanup:
        clean_up(output)
    if output["foundIssues"]:
        sys.exit(1)
    else:
        sys.exit(0)


def str2bool(v):
    if v is None:
        return True
    if v.lower() in ('yes', 'true', 't', 'y', '1'):
        return True
    elif v.lower() in ('no', 'false', 'f', 'n', '0'):
        return False
    else:
        raise argparse.ArgumentTypeError('Boolean value expected.')


BASE64_CHARS = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/="
HEX_CHARS = "1234567890abcdefABCDEF"


def del_rw(action, name, exc):
    os.chmod(name, stat.S_IWRITE)
    os.remove(name)


def shannon_entropy(data, iterator):
    """
    Borrowed from http://blog.dkbza.org/2007/05/scanning-data-for-entropy-anomalies.html
    """
    if not data:
        return 0
    entropy = 0
    for x in iterator:
        p_x = float(data.count(x))/len(data)
        if p_x > 0:
            entropy += - p_x*math.log(p_x, 2)
    return entropy


def get_strings_of_set(word, char_set, threshold=20):
    count = 0
    letters = ""
    strings = []
    for char in word:
        if char in char_set:
            letters += char
            count += 1
        else:
            if count > threshold:
                strings.append(letters)
            letters = ""
            count = 0
    if count > threshold:
        strings.append(letters)
    return strings


class bcolors:
    HEADER = '\033[95m'
    OKBLUE = '\033[94m'
    OKGREEN = '\033[92m'
    WARNING = '\033[93m'
    FAIL = '\033[91m'
    ENDC = '\033[0m'
    BOLD = '\033[1m'
    UNDERLINE = '\033[4m'


def clone_git_repo(git_url):
    project_path = tempfile.mkdtemp()
    Repo.clone_from(git_url, project_path)
    return project_path


def print_results(printJson, issue, contextify):
    commit_time = issue['date']
    branch_name = issue['branch']
    prev_commit = issue['commit']

    if contextify:
        printableDiff = issue['printContext']
    else:
        printableDiff = issue['printDiff']

    commitHash = issue['commitHash']
    reason = issue['reason']
    path = issue['path']

    if printJson:
        print(json.dumps(issue, sort_keys=True))
    else:
        print("~~~~~~~~~~~~~~~~~~~~~")
        reason = "{}Reason: {}{}".format(bcolors.OKGREEN, reason, bcolors.ENDC)
        print(reason)
        dateStr = "{}Date: {}{}".format(bcolors.OKGREEN, commit_time, bcolors.ENDC)
        print(dateStr)
        hashStr = "{}Hash: {}{}".format(bcolors.OKGREEN, commitHash, bcolors.ENDC)
        print(hashStr)
        filePath = "{}Filepath: {}{}".format(bcolors.OKGREEN, path, bcolors.ENDC)
        print(filePath)

        if sys.version_info >= (3, 0):
            branchStr = "{}Branch: {}{}".format(bcolors.OKGREEN, branch_name, bcolors.ENDC)
            print(branchStr)
            commitStr = "{}Commit: {}{}".format(bcolors.OKGREEN, prev_commit, bcolors.ENDC)
            print(commitStr)
            print(printableDiff)
        else:
            branchStr = "{}Branch: {}{}".format(bcolors.OKGREEN, branch_name.encode('utf-8'), bcolors.ENDC)
            print(branchStr)
            commitStr = "{}Commit: {}{}".format(bcolors.OKGREEN, prev_commit.encode('utf-8'), bcolors.ENDC)
            print(commitStr)
            print(printableDiff.encode('utf-8'))
        print("~~~~~~~~~~~~~~~~~~~~~")


def render_with_context(lines_with_context):
    return "\n\n".join(["Line {}: \n".format(line_with_context[0])  # line number
                        + line_with_context[1][0] + "\n"  # pre context
                        # the middle term holds the found entity
                        + bcolors.WARNING + line_with_context[1][1] + bcolors.ENDC + "\n"
                        + line_with_context[1][2]  # post context
                        for line_with_context in lines_with_context])


def process_found_word_with_context(string, line_number, word_number, all_words):
    result = (line_number, [  # a tuple
        all_words[max(0, word_number - 1)],
        string,
        all_words[min(len(all_words) - 1, word_number + 1)],
    ])
    return result


def find_entropy(printableDiff, commit_time, branch_name, prev_commit, blob, commitHash):
    lines_with_context_found = []
    lines = printableDiff.split("\n")
    for line, line_number in zip(lines, range(len(lines))):
        words = line.split()
        for word_number in range(len(words)):
            word = words[word_number]
            base64_strings = get_strings_of_set(word, BASE64_CHARS)
            hex_strings = get_strings_of_set(word, HEX_CHARS)
            for string in base64_strings:
                b64Entropy = shannon_entropy(string, BASE64_CHARS)
                if b64Entropy > 4.5:
                    lines_with_context_found.append(
                        process_found_word_with_context(string, line_number,
                                                        word_number, words))
                    printableDiff = printableDiff.replace(string, bcolors.WARNING + string + bcolors.ENDC)
            for string in hex_strings:
                hexEntropy = shannon_entropy(string, HEX_CHARS)
                if hexEntropy > 3:
                    lines_with_context_found.append(
                        process_found_word_with_context(string, line_number,
                                                        word_number, words))
                    printableDiff = printableDiff.replace(string, bcolors.WARNING + string + bcolors.ENDC)
    entropicDiff = None
    if len(lines_with_context_found) > 0:
        entropicDiff = {}
        entropicDiff['date'] = commit_time
        entropicDiff['path'] = blob.b_path if blob.b_path else blob.a_path
        entropicDiff['branch'] = branch_name
        entropicDiff['commit'] = prev_commit.message
        entropicDiff['diff'] = blob.diff.decode('utf-8', errors='replace')
        entropicDiff['stringsFound'] = lines_with_context_found
        entropicDiff['printContext'] = render_with_context(lines_with_context_found)
        entropicDiff['printDiff'] = printableDiff
        entropicDiff['commitHash'] = prev_commit.hexsha
        entropicDiff['reason'] = "High Entropy"
    return entropicDiff


def regex_check(printableDiff, commit_time, branch_name, prev_commit, blob, commitHash, custom_regexes={}):
    if custom_regexes:
        secret_regexes = custom_regexes
    else:
        secret_regexes = regexes
    regex_matches = []
    for key in secret_regexes:
        found_strings = secret_regexes[key].findall(printableDiff)
        for found_string in found_strings:
            found_diff = printableDiff.replace(printableDiff, bcolors.WARNING + found_string + bcolors.ENDC)
        if found_strings:
            foundRegex = {}
            foundRegex['date'] = commit_time
            foundRegex['path'] = blob.b_path if blob.b_path else blob.a_path
            foundRegex['branch'] = branch_name
            foundRegex['commit'] = prev_commit.message
            foundRegex['diff'] = blob.diff.decode('utf-8', errors='replace')
            foundRegex['stringsFound'] = found_strings
            foundRegex['printDiff'] = found_diff
            foundRegex['printContext'] = "\n\n".implode(found_strings)
            foundRegex['reason'] = key
            foundRegex['commitHash'] = prev_commit.hexsha
            regex_matches.append(foundRegex)
    return regex_matches


def diff_worker(diff, curr_commit, prev_commit, branch_name, commitHash, custom_regexes, do_entropy,
                do_regex, printJson, surpress_output, contextify):
    issues = []
    for blob in diff:
        printableDiff = blob.diff.decode('utf-8', errors='replace')
        if printableDiff.startswith("Binary files"):
            continue
        commit_time =  datetime.datetime.fromtimestamp(prev_commit.committed_date).strftime('%Y-%m-%d %H:%M:%S')
        foundIssues = []
        if do_entropy:
            entropicDiff = find_entropy(printableDiff, commit_time, branch_name, prev_commit, blob, commitHash)
            if entropicDiff:
                foundIssues.append(entropicDiff)
        if do_regex:
            found_regexes = regex_check(printableDiff, commit_time, branch_name, prev_commit, blob, commitHash, custom_regexes)
            foundIssues += found_regexes
        if not surpress_output:
            for foundIssue in foundIssues:
                print_results(printJson, foundIssue, contextify)
        issues += foundIssues
    return issues


def handle_results(output, output_dir, foundIssues):
    for foundIssue in foundIssues:
        result_path = os.path.join(output_dir, str(uuid.uuid4()))
        with open(result_path, "w+") as result_file:
            result_file.write(json.dumps(foundIssue))
        output["foundIssues"].append(result_path)
    return output


def find_strings(git_url, since_commit=None, max_depth=1000000, printJson=False, do_regex=False,
                 do_entropy=True, surpress_output=True, custom_regexes={},
                 branch=None, repo_path=None, contextify=False):
    output = {"foundIssues": []}
    if repo_path:
        project_path = repo_path
    else:
        project_path = clone_git_repo(git_url)
    repo = Repo(project_path)
    already_searched = set()
    output_dir = tempfile.mkdtemp()

    if branch:
        branches = repo.remotes.origin.fetch(branch)
    else:
        branches = repo.remotes.origin.fetch()

    for remote_branch in branches:
        since_commit_reached = False
        branch_name = remote_branch.name
        prev_commit = None
        for curr_commit in repo.iter_commits(branch_name, max_count=max_depth):
            commitHash = curr_commit.hexsha
            if commitHash == since_commit:
                since_commit_reached = True
            if since_commit and since_commit_reached:
                prev_commit = curr_commit
                continue
            # if not prev_commit, then curr_commit is the newest commit. And we have nothing to diff with.
            # But we will diff the first commit with NULL_TREE here to check the oldest code.
            # In this way, no commit will be missed.
            diff_hash = hashlib.md5((str(prev_commit) + str(curr_commit)).encode('utf-8')).digest()
            if not prev_commit:
                prev_commit = curr_commit
                continue
            elif diff_hash in already_searched:
                prev_commit = curr_commit
                continue
            else:
                diff = prev_commit.diff(curr_commit, create_patch=True)
            # avoid searching the same diffs
            already_searched.add(diff_hash)
            foundIssues = diff_worker(diff, curr_commit, prev_commit, branch_name, commitHash, custom_regexes,
                                      do_entropy, do_regex, printJson, surpress_output, contextify)
            output = handle_results(output, output_dir, foundIssues)
            prev_commit = curr_commit
        # Handling the first commit
        diff = curr_commit.diff(NULL_TREE, create_patch=True)
        foundIssues = diff_worker(diff, curr_commit, prev_commit, branch_name, commitHash, custom_regexes,
                                  do_entropy, do_regex, printJson, surpress_output, contextify)
        output = handle_results(output, output_dir, foundIssues)
    output["project_path"] = project_path
    output["clone_uri"] = git_url
    output["issues_path"] = output_dir
    if not repo_path:
        shutil.rmtree(project_path, onerror=del_rw)
    return output


def clean_up(output):
    issues_path = output.get("issues_path", None)
    if issues_path and os.path.isdir(issues_path):
        shutil.rmtree(output["issues_path"])


if __name__ == "__main__":
    main()
