#################################################################
# MET v2 Metadate Explorer Tool
#
# This Software is Open Source. See License: https://github.com/TERENA/met/blob/master/LICENSE.md
# Copyright (c) 2012, TERENA All rights reserved.
#
# This Software is based on MET v1 developed for TERENA by Yaco Sistemas, http://www.yaco.es/
# MET v2 was developed for TERENA by Tamim Ziai, DAASI International GmbH, http://www.daasi.de
# Current version of MET has been revised for performance improvements by Andrea Biancini,
# Consortium GARR, http://www.garr.it
#########################################################################################

import sys, os
import logging
import logging.config
from optparse import OptionParser

os.environ['PYTHON_EGG_CACHE'] = '/tmp/.python-eggs/'

current_directory = os.path.join(os.path.dirname(__file__), '..')
activate_this = os.path.join(current_directory, '..', 'met-venv/bin/activate_this.py')
execfile(activate_this, dict(__file__=activate_this))

sys.path.append(current_directory)
sys.path.append(os.path.join(current_directory, 'met'))
os.environ['DJANGO_SETTINGS_MODULE'] = 'met.settings'

import django
from met.metadataparser.refresh_metadata import refresh

django.setup()

class SingleRun():
    class InstanceRunningException(Exception):
        pass

    def __init__(self, lock_file):
        #define the lock file name
        self.lock_file =  "/tmp/%s.pid" % lock_file

    def __call__(self, func):
        def fnc(*args, **kwargs):
            if os.path.exists(self.lock_file):
                #get process id, if lock file exists
                pid = open(self.lock_file, "rt").read()
                if not os.path.exists("/proc/%s" % pid):
                    #if process is not alive remove the lock file
                    os.unlink(self.lock_file)
                else:
                    #process is running
                    raise self.InstanceRunningException(pid)

            try:
                #store process id
                open(self.lock_file, "wt").write(str(os.getpid()))
                #execute wrapped function
                func(*args,**kwargs)
            finally:
                if os.path.exists(self.lock_file):
                    os.unlink(self.lock_file)
        return fnc

class RefreshMetaData:
    @classmethod
    def process(cls, options):
        fed_name = options.fed_name
        force_refresh = options.force_refresh
        
        logger = None
        if options.log:
            logging.config.fileConfig(options.log)
            logger = logging.getLogger("Refresh")
    
        try:
            refresh(fed_name, force_refresh, logger)
        except Exception as e:
            if logger:
	        logger.error("%s" % e)

@SingleRun(lock_file="met-metadatarefresh")
def commandline_call(argv, convert_class=RefreshMetaData):
    opt_parser = OptionParser()
    opt_parser.set_usage("refresh [--federation <fed_name>] [--log  <file>] [--force-refresh]")
    
    opt_parser.add_option(
        "-l",
        "--log",
        type="string",
        dest="log",
        help="The logger configuration file",
        default=None,
        metavar="LOG")

    opt_parser.add_option(
        "-f",
        "--federation",
        type="string",
        dest="fed_name",
        help="The federation to be updated (None for anyone)",
        default=None,
        metavar="FED")

    opt_parser.add_option(
        "-r",
        "--force-refresh",
        action="store_true",
        dest="force_refresh",
        help="Force refresh of metadata information (even if file has not changed)",
        metavar="REF")

    (options, args) = opt_parser.parse_args()
    
    error_message = ""
    if options.log and not os.path.exists(options.log):
        error_message = "File '%s' does not exist." % options.log
    
    if error_message:
        print(error_message)
        print(opt_parser.get_usage())
        exit (1)
    
    obj_convert = convert_class()
    obj_convert.process(options)

if __name__ == '__main__':
    log_args = {'level': logging.ERROR}
    logging.basicConfig(**log_args)

    commandline_call(sys.argv)
