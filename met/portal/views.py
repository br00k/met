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

from django.shortcuts import render_to_response
from django.template import RequestContext


def error403(request):
    return render_to_response('403.html', {
           }, context_instance=RequestContext(request))


def error404(request):
    return render_to_response('404.html', {
           }, context_instance=RequestContext(request))


def error500(request):
    return render_to_response('500.html', {
           }, context_instance=RequestContext(request))
