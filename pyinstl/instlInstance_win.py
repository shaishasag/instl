#!/usr/local/bin/python2.7
from __future__ import print_function

import instlInstanceBase
import os
from copyCommander import CopyCommander_win_robocopy

def quoteme(to_qoute):
    return "".join( ('"', to_qoute, '"') )


class InstlInstance_win(instlInstanceBase.InstlInstanceBase):
    def __init__(self):
        super(InstlInstance_win, self).__init__()
        self.var_replacement_pattern = "%\g<var_name>%"

    def get_install_instructions_prefix(self):
        return ("SET SAVE_DIR=%CD%", )

    def get_install_instructions_postfix(self):
        return ("chdir /d %SAVE_DIR%", )

    def make_directory_cmd(self, directory):
        mk_command = " ".join( ("mkdir", '"'+directory+'"'))
        return (mk_command, )
 
    def change_directory_cmd(self, directory):
        cd_command = " ".join( ("cd", '"'+directory+'"') )
        return (cd_command, )

    def get_svn_folder_cleanup_instructions(self):
        return ()
        
    def create_var_assign(self, identifier, value):
        return "SET "+identifier+'='+value

    def create_echo_command(self, message, file=None):
        echo_command = " ".join(('echo', quoteme(self.echo_indent_level * 4 * " "+message)))
        if file:
            echo_command = " ".join((echo_command, ">>", file))
        return echo_command

    def create_remark_command(self, remark):
        remark_command = " ".join(('REM', quoteme(remark)))
        return remark_command
