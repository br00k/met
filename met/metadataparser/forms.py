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

from django import forms
from django.utils.translation import ugettext_lazy as _
from django.forms.widgets import CheckboxSelectMultiple, Widget
from django.forms.extras.widgets import SelectDateWidget
from django.forms.util import ErrorDict, flatatt

from django.utils import timezone
from dateutil.relativedelta import relativedelta

from django.utils.html import format_html
from django.utils.safestring import mark_safe
from met.metadataparser.models import Federation, Entity

class MultiURLforMetadata(Widget):
    def render(self, name, value, attrs=None, choices=()):
        if value is None:
            value = []
	 
        final_attrs = self.build_attrs(attrs, name=name)
	output = []
        output.append(format_html('<table id="metadata_type" class="display" cellspacing="0" width="100%"><thead><tr><th>Metadata</th><th>Type</th></tr></thead><tbody>', flatatt(final_attrs)))
	for curpair in value.split("|"):
	    val = ''.join(curpair)
            val = curpair.split(";")

            if len(val) == 1:
                val.append("All")

	    output.append('<tr><td>%s</th><td>%s</td></tr>' % (val[0], val[1]))

	output.append('''
            </tbody></table>
            <br/>

            <button id="delete" type="button">Delete selected URL</button>
            <br/><br/><br/>

            <fieldset class="control-group" id="new_URL_set">
            Meta URL: <input type="url" name="meta_URL" id="meta_URL" />
            <select name="type_URL" id="type_URL"><option value="All">All</option><option value="IDP">IDP</option><option value="SP">SP</option></select>
            <input id="add" type="button" value="Add URL" />
            </fieldset>
        ''')

        output.append('<input type="hidden" id="id_%s" name="%s" value=""><br/><br/>' % (name, name))

        output.append('''<script>
            $(document).ready(function() {
                $.extend( $.fn.dataTable.defaults, {
                    "searching": false,
                    "ordering":  false,
                    "paging":    false,
                    "info":      false
                }); 
   
                var table = $('#metadata_type').DataTable();
                $('#metadata_type tbody').on( 'click', 'tr', function () {
                    $(this).toggleClass('selected');
                });
                var text = "";
                table.rows().every( function () {
                    var data = this.data();
                    text += data[0] +  ";" + data[1] + "|";
                } );
		text = text.substring(0, text.length - 1);
		$('#id_%s').val(text);

                $('#add').click( function () {
                    if ($('#meta_URL').val() == undefined) return;
		    texturl = $('#meta_URL').val();
                    var urlpattern = new RegExp("(http|ftp|https)://[\\w-]+(\\.[\\w-]+)+([\\w.,@?^=%%&amp;:/~+#-]*[\\w@?^=%%&amp;/~+#-])?");
                    if (!urlpattern.test($('#meta_URL').val())) {
                        $('#new_URL_set').addClass("error");
			return; 
		    }

                    $('#new_URL_set').removeClass("error");
                    table.row.add([$('#meta_URL').val(), $('#type_URL').val()]).draw();
                    $('#meta_URL').val("");
                    $('#type_URL').val("All");

                    var text = "";
                    table.rows().every( function () {
                        var data = this.data();
                        text +=data[0] +  ";" + data[1] + "|";
                    } );
		    text = text.substring(0, text.length - 1);
		    $('#id_%s').val(text);
                });

                $('#delete').click( function () {
                    table.row('.selected').remove().draw(false);
		   
		    var text = "";
                    table.rows().every( function () {
                        var data = this.data();
                        text +=data[0] +  ";" + data[1] + "|";
                    } );
		    text = text.substring(0, text.length - 1);
                    $('#id_%s').val(text); 
                });
            });
            </script>''' % (name, name, name))

        return mark_safe('\n'.join(output))


class FederationForm(forms.ModelForm):

    def __init__(self, *args, **kwargs):
        super(FederationForm, self).__init__(*args, **kwargs)
        editor_users_choices = self.fields['editor_users'].widget.choices
        self.fields['editor_users'].widget = CheckboxSelectMultiple(
                                                choices=editor_users_choices)
        self.fields['editor_users'].help_text = _("This/these user(s) can edit this "
                                                 "federation and its entities")

        self.fields['file_url'].widget = MultiURLforMetadata()

    class Meta:
        model = Federation
        fields = ['name', 'url', 'registration_authority', 'country', 'logo', 'is_interfederation', 'type', 'fee_schedule_url', 'file_url', 'file', 'editor_users']


class EntityForm(forms.ModelForm):

    def __init__(self, *args, **kwargs):
        super(EntityForm, self).__init__(*args, **kwargs)
        editor_users_choices = self.fields['editor_users'].widget.choices
        self.fields['editor_users'].widget = CheckboxSelectMultiple(
                                                choices=editor_users_choices)
        self.fields['editor_users'].help_text = _("These users can edit only "
                                                  "this entity")

    class Meta:
        model = Entity
        fields = ['registration_authority', 'file_url', 'file', 'editor_users']

class ChartForm(forms.Form):
    fromDate = forms.DateField(label=_(u'Start date'),
                             help_text=_(u"Statistics start date."), initial=timezone.now()-relativedelta(months=1),
                             widget=SelectDateWidget(years=range(timezone.datetime.today().year, 2012, -1)))

    toDate = forms.DateField(label=_(u'End date'),
                             help_text=_(u"Statistics end date."), initial=timezone.now(),
                             widget=SelectDateWidget(years=range(timezone.datetime.today().year, 2012, -1)))

    def is_valid(self):
        result = super(ChartForm, self).is_valid()

        if result:
            result = self.cleaned_data['fromDate'] <= self.cleaned_data['toDate']
            if not result:
                errors = ErrorDict()
                errors['toDate'] = 'End date must not be before Start date'
                self._errors = errors

        return result
            
    def __init__(self, *args, **kwargs):
        self.instance = kwargs.pop('instance')
        super(ChartForm, self).__init__(*args, **kwargs)

    class Meta:
        exclude = []


class EntityCommentForm(forms.Form):
    email = forms.EmailField(label=_(u'Your email address'),
                             help_text=_(u"Please enter your email address here."))

    comment = forms.CharField(max_length=1000, label=_(u"Your comment"),
                             help_text=_(u"Please enter your comment here."),
                             widget=forms.Textarea(attrs={'cols': '100', 'rows': '10'}))

    def __init__(self, *args, **kwargs):
        self.instance = kwargs.pop('instance')
        super(EntityCommentForm, self).__init__(*args, **kwargs)

    class Meta:
        exclude = []


class EntityProposalForm(forms.Form):
    email = forms.EmailField(label=_(u'Your email address'),
                             help_text=_(u"Please enter your email address here."))

    federation_choices = []
    i = 0
    for federation in Federation.objects.all():
        i += i
        federation_choices.append(('%s' %federation, federation))

    federations = forms.MultipleChoiceField(label=_(u'Federations'), choices = federation_choices,
                             help_text=_(u"Please select the federation(s) you want to gather the entity in."))
    
    comment = forms.CharField(max_length=1000, label=_(u"Your comment"),
                             help_text=_(u"Please enter your comment here."),
                             widget=forms.Textarea(attrs={'cols': '100', 'rows': '10'}))
    

    def __init__(self, *args, **kwargs):
        self.instance = kwargs.pop('instance')
        super(EntityProposalForm, self).__init__(*args, **kwargs)

        gatherd_federations = self.instance.federations.all()
        federation_choices = []
        i = 0
        for federation in Federation.objects.all().order_by('name'):
            if federation not in gatherd_federations:
                i += i
                federation_choices.append(('%s' %federation, federation))
            
        self.fields['federations'].widget.choices = federation_choices
                             
 
    class Meta:
        exclude = []


class ServiceSearchForm(forms.Form):
    entityid = forms.CharField(max_length=200, label=_(u"Search service ID"),
                             help_text=_(u"Enter a full or partial entityid"),
                             widget=forms.TextInput(attrs={'size': '200'}))

    class Meta:
        exclude = []
