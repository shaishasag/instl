#!/usr/bin/env python2.7
from __future__ import print_function

import os
import urllib
import datetime

from platformSpecificHelper_Base import PlatformSpecificHelperBase
from platformSpecificHelper_Base import CopyToolRsync
from platformSpecificHelper_Base import DownloadToolBase
from platformSpecificHelper_Base import quoteme_single
from platformSpecificHelper_Base import quoteme_double

class CopyToolMacRsync(CopyToolRsync):
    pass

class PlatformSpecificHelperMac(PlatformSpecificHelperBase):
    def __init__(self, instlInstance):
        super(PlatformSpecificHelperMac, self).__init__(instlInstance)
        self.var_replacement_pattern = "${\g<var_name>}"
        self.dl_tool = DownloadTool_mac_curl()

    def get_install_instructions_prefix(self):
        """ exec 2>&1 within a batch file will redirect stderr to stdout.
            .sync.sh >& out.txt on the command line will redirect stderr to stdout from without.
        """
        retVal = (
            "#!/bin/sh",
            self.remark(self.instlInstance.get_version_str()),
            self.remark(datetime.datetime.today().isoformat()),
            "set -e",
            self.save_dir("TOP_SAVE_DIR"))
        return retVal

    def get_install_instructions_postfix(self):
        return (self.restore_dir("TOP_SAVE_DIR"), "exit 0")

    def mkdir(self, directory):
        mk_command = " ".join( ("mkdir", "-p", quoteme_double(directory) ) )
        return mk_command

    def cd(self, directory):
        cd_command = " ".join( ("cd", quoteme_double(directory) ) )
        return cd_command

    def save_dir(self, var_name):
        save_dir_command = var_name+"=`pwd`"
        return save_dir_command

    def restore_dir(self, var_name):
        restore_dir_command = self.cd("$("+var_name+")")
        return restore_dir_command

    def rmdir(self, directory, recursive=False):
        rmdir_command = ""
        if recursive:
            rmdir_command = " ".join( ("rm", "-fr", quoteme_double(directory) ) )
        else:
            rmdir_command = " ".join( ("rmdir", quoteme_double(directory) ) )
        return rmdir_command

    def rmfile(self, file):
        rmfile_command = " ".join( ("rm", "-f", quoteme_double(file) ) )
        return rmfile_command

    def get_svn_folder_cleanup_instructions(self):
        return 'find . -maxdepth 1 -mindepth 1 -type d -print0 | xargs -0 "$(SVN_CLIENT_PATH)" cleanup --non-interactive'

    def var_assign(self, identifier, value):
        return identifier+'="'+value+'"'

    def echo(self, message):
        echo_command = " ".join(('echo', quoteme_double(message)))
        return echo_command

    def remark(self, remark):
        remark_command = " ".join(('#', remark))
        return remark_command

    def use_copy_tool(self, tool):
        if tool == "rsync":
            self.copy_tool = CopyToolMacRsync()
        else:
            raise ValueError(tool, "is not a valid copy tool for Mac OS")

    def copy_file_to_file(self, src_file, trg_file):
        sync_command = "cp -f \"{src_file}\" \"{trg_file}\"".format(**locals())
        return sync_command

    def resolve_symlink_files(self, in_dir="."):
        """ create instructions to turn .readlink files into symlinks.
            Main problem was with files that had space in their name, just
            adding \" was no enough, had to separate each step to a single line
            which solved the spaces problem. Also find returns an empty string
            even when there were no files found, and therefor the check
        """
        resolve_commands = ("""
find -P "%s" -type f -name '*.symlink' | while read readlink_file; do
    link_target=${readlink_file%%.*}
    if [ ! -h "${link_target}" ]
    then
        symlink_contents=`cat "${readlink_file}"`
        ln -sfh "${symlink_contents}" "${link_target}"
    fi
done""" % in_dir)
        return resolve_commands

class DownloadTool_mac_curl(DownloadToolBase):
    def __init__(self):
        self.curl_instructions = list()

    def add_dl(self, url, path):
        self.curl_instructions.append( (urllib.quote(url, "$()/:"), path) )

    def create_download_file_to_file_command(self, src_url, trg_file):
        download_command_parts = list()
        download_command_parts.append("$(__RESOLVED_DOWNLOAD_TOOL_PATH__)")
        download_command_parts.append("--insecure")
        download_command_parts.append("--fail")
        download_command_parts.append("--raw")
        download_command_parts.append("--silent")
        download_command_parts.append("--show-error")
        download_command_parts.append("--compressed")
        download_command_parts.append("--connect-timeout")
        download_command_parts.append("60")
        download_command_parts.append("--max-time")
        download_command_parts.append("900")
        download_command_parts.append("-o")
        download_command_parts.append(quoteme_double(trg_file))
        download_command_parts.append(quoteme_double(urllib.quote(src_url, "$()/:")))
        return " ".join(download_command_parts)

    def create_config_file(self, curl_config_file_path):
        with open(curl_config_file_path, "w") as wfd:
            wfd.write("insecure\n")
            wfd.write("raw\n")
            wfd.write("fail\n")
            wfd.write("silent\n")
            wfd.write("show-error\n")
            wfd.write("compressed\n")
            wfd.write("create-dirs\n")
            wfd.write("connect-timeout = 60\n")
            wfd.write("\n")
            for url, path in self.curl_instructions:
                wfd.write('''url = "{url}"\noutput = "{path}"\n\n'''.format(**locals()))

    def create_download_from_config_file(self, config_file):

        download_command_parts = list()
        download_command_parts.append("$(__RESOLVED_DOWNLOAD_TOOL_PATH__)")
        download_command_parts.append("--max-time")
        download_command_parts.append(str(len(self.curl_instructions) * 6 + 300)) # 6 seconds for each item + 5 minutes
        download_command_parts.append("--config")
        download_command_parts.append(config_file)

        return " ".join(download_command_parts)
