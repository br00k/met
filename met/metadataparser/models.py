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

import requests
import simplejson as json

from os import path
from urlparse import urlsplit, urlparse
from urllib import quote_plus
from datetime import datetime

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.models import User
from django.core import validators
from django.core.cache import get_cache
from django.core.files.base import ContentFile
from django.core.urlresolvers import reverse
from django.db import models, transaction
from django.db.models import Count
from django.db.models.signals import pre_save
from django.db.models.query import QuerySet
from django.dispatch import receiver
from django.template.defaultfilters import slugify
from django.utils.safestring import mark_safe
from django.utils.translation import ugettext_lazy as _
from django.utils import timezone

from lxml import etree

from pyff.mdrepo import MDRepository
from pyff.pipes import Plumbing

from met.metadataparser.utils import compare_filecontents
from met.metadataparser.xmlparser import MetadataParser, DESCRIPTOR_TYPES_DISPLAY
from met.metadataparser.templatetags import attributemap


TOP_LENGTH = getattr(settings, "TOP_LENGTH", 5)
stats = getattr(settings, "STATS")

FEDERATION_TYPES = (
    (None, ''),
    ('hub-and-spoke', 'Hub and Spoke'),
    ('mesh', 'Full Mesh'),
)


def update_obj(mobj, obj, attrs=None):
    for_attrs = attrs or getattr(mobj, 'all_attrs', [])
    for attrb in attrs or for_attrs:
        if (getattr(mobj, attrb, None) and
            getattr(obj, attrb, None) and
            getattr(mobj, attrb) != getattr(obj, attrb)):
            setattr(obj, attrb,  getattr(mobj, attrb))

class JSONField(models.CharField):
    """JSONField is a generic textfield that neatly serializes/unserializes
    JSON objects seamlessly

    The json spec claims you must use a collection type at the top level of
    the data structure.  However the simplesjon decoder and Firefox both encode
    and decode non collection types that do not exist inside a collection.
    The to_python method relies on the value being an instance of basestring
    to ensure that it is encoded.  If a string is the sole value at the
    point the field is instanced, to_python attempts to decode the sting because
    it is derived from basestring but cannot be encodeded and throws the
    exception ValueError: No JSON object could be decoded.
    """

    # Used so to_python() is called
    __metaclass__ = models.SubfieldBase
    description = _("JSON object")

    def __init__(self, *args, **kwargs):
        super(JSONField, self).__init__(*args, **kwargs)
        self.validators.append(validators.MaxLengthValidator(self.max_length))

    @classmethod
    def get_internal_type(cls):
        return "TextField"

    @classmethod
    def to_python(cls, value):
        """Convert our string value to JSON after we load it from the DB"""
        if value == "":
            return None

        try:
            if isinstance(value, basestring):
                return json.loads(value)
        except ValueError:
            return value

        return value

    def get_prep_value(self, value):
        """Convert our JSON object to a string before we save"""

        if not value or value == "":
            return None

        db_value = json.dumps(value)
        return super(JSONField, self).get_prep_value(db_value)

    def get_db_prep_value(self, value, connection, prepared=False):
        """Convert our JSON object to a string before we save"""

        if not value or value == "":
            return None

        db_value = json.dumps(value)
        return super(JSONField, self).get_db_prep_value(db_value, connection, prepared)


class Base(models.Model):
    file_url = models.CharField(verbose_name='Metadata url',
                                max_length=1000,
                                blank=True, null=True,
                                help_text=_(u'Url to fetch metadata file'))
    file = models.FileField(upload_to='metadata', blank=True, null=True,
                            verbose_name=_(u'metadata xml file'),
                            help_text=_("if url is set, metadata url will be "
                                        "fetched and replace file value"))
    file_id = models.CharField(blank=True, null=True, max_length=500,
                               verbose_name=_(u'File ID'))

    registration_authority = models.CharField(verbose_name=_('Registration Authority'),
                                              max_length=200, blank=True, null=True)

    editor_users = models.ManyToManyField(User, null=True, blank=True,
                                          verbose_name=_('editor users'))

    class Meta:
        abstract = True

    class XmlError(Exception):
        pass

    def __unicode__(self):
        return self.url or u"Metadata %s" % self.id

    def load_file(self):
        if not hasattr(self, '_loaded_file'):
            """Only load file and parse it, don't create/update any objects"""
            if not self.file:
                return None
            self._loaded_file = MetadataParser(filename=self.file.path)
        return self._loaded_file

    def _get_metadata_stream(self, load_streams):
        try:
            load = []
            select = []

            count = 1
            for stream in load_streams:
                curid = "%s%d" % (self.slug, count)
                load.append("%s as %s" % (stream[0], curid))
                if stream[1] == 'SP' or stream[1] == 'IDP':
                    select.append("%s!//md:EntityDescriptor[md:%sSSODescriptor]" % (curid, stream[1]))
                else:
                    select.append("%s" % curid)
                count = count + 1

            if len(select) > 0:
                pipeline = [{'load': load}, {'select': select}]
            else:
                pipeline = [{'load': load}, 'select']

            md = MDRepository()
            entities = Plumbing(pipeline=pipeline, id=self.slug).process(md, state={'batch': True, 'stats': {}})
            return etree.tostring(entities)
        except:
            raise Exception('Getting metadata from %s failed.' % load_streams)

    def fetch_metadata_file(self, file_name):
        file_url = self.file_url
        if not file_url or file_url == '':
            return

        metadata_files = []
        files = file_url.split("|")
        for curfile in files:
            cursource = curfile.split(";")
            if len(cursource) == 1:
                cursource.append("All")
            metadata_files.append(cursource)

        req = self._get_metadata_stream(metadata_files)

        try:
            self.file.seek(0)
            original_file_content = self.file.read()
            if compare_filecontents(original_file_content, req):
                return False
        except IOError:
            pass

        filename = path.basename("%s-metadata.xml" % file_name)
        self.file.delete(save=False)
        self.file.save(filename, ContentFile(req), save=False)
        return True

    @classmethod
    def process_metadata(cls):
        raise NotImplemented()


class XmlDescriptionError(Exception):
    pass


class Federation(Base):

    name = models.CharField(blank=False, null=False, max_length=200,
                            unique=True, verbose_name=_(u'Name'))

    type = models.CharField(blank=True, null=True, max_length=100,
                            unique=False, verbose_name=_(u'Type'), choices=FEDERATION_TYPES)

    url = models.URLField(verbose_name='Federation url',
                          blank=True, null=True)
    
    fee_schedule_url = models.URLField(verbose_name='Fee schedule url',
                                       max_length=150, blank=True, null=True)

    logo = models.ImageField(upload_to='federation_logo', blank=True,
                             null=True, verbose_name=_(u'Federation logo'))
    is_interfederation = models.BooleanField(default=False, db_index=True,
                                         verbose_name=_(u'Is interfederation'))
    slug = models.SlugField(max_length=200, unique=True)

    country = models.CharField(blank=True, null=True, max_length=100,
                               unique=False, verbose_name=_(u'Country'))

    metadata_update = models.DateField(blank=True, null=True,
                                       unique=False, verbose_name=_(u'Metadata update date'))

    @property
    def _metadata(self):
        if not hasattr(self, '_metadata_cache'):
            self._metadata_cache = self.load_file()
        return self._metadata_cache

    def __unicode__(self):
        return self.name

    def get_entity_metadata(self, entityid):
        return self._metadata.get_entity(entityid)

    def get_entity(self, entityid):
        return self.entity_set.get(entityid=entityid)

    def process_metadata(self):
        metadata = self.load_file()

        if self.file_id and metadata.file_id and metadata.file_id == self.file_id:
            return
        else:
            self.file_id = metadata.file_id

        if not metadata:
            return
        if not metadata.is_federation:
            raise XmlDescriptionError("XML Haven't federation form")

        update_obj(metadata.get_federation(), self)

    def _remove_deleted_entities(self, entities_from_xml, request):
        entities_to_remove = []
        for entity in self.entity_set.all():
            """Remove entity relation if does not exist in metadata"""
            if not entity.entityid in entities_from_xml:
                entities_to_remove.append(entity)

        if len(entities_to_remove) > 0:
            self.entity_set.remove(*entities_to_remove)

            if request and not entity.federations.exists():
                for entity in entities_to_remove:
                    messages.warning(request,
                                     mark_safe(_("Orphan entity: <a href='%s'>%s</a>" %
                                     (entity.get_absolute_url(), entity.entityid))))

        return len(entities_to_remove)

    def _update_entities(self, entities_to_update, entities_to_add):
        for e in entities_to_update:
            e.save()

        self.entity_set.add(*entities_to_add)

    @staticmethod
    def _entity_has_changed(entity, entityid, name, registration_authority):
        if entity.entityid != entityid:
            return True
        if entity.name != name:
            return True
        if entity.registration_authority != registration_authority:
            return True

        return False

    def _add_new_entities(self, entities, entities_from_xml, request, federation_slug):
        db_entity_types = EntityType.objects.all()
        cached_entity_types = { entity_type.xmlname: entity_type for entity_type in db_entity_types }

        entities_to_add = []
        entities_to_update = []

        for m_id in entities_from_xml:
            if request and federation_slug:
                request.session['%s_cur_entities' % federation_slug] += 1
                request.session.save()

            created = False
            if m_id in entities:
                entity = entities[m_id]
            else:
                entity, created = Entity.objects.get_or_create(entityid=m_id)

            entityid = entity.entityid
            name = entity.name
            registration_authority = entity.registration_authority
 
            entity_from_xml = self._metadata.get_entity(m_id, True)
            entity.process_metadata(False, entity_from_xml, cached_entity_types)

            if created or self._entity_has_changed(entity, entityid, name, registration_authority):
                entities_to_update.append(entity)

            entities_to_add.append(entity)

        self._update_entities(entities_to_update, entities_to_add)
        return len(entities_to_update) 

    def compute_new_stats(self, timestamp=timezone.now()):
        entities_from_xml = self._metadata.get_entities()

        entities = Entity.objects.filter(entityid__in=entities_from_xml)
        entities = entities.prefetch_related('types')

        entity_stats = []
        for feature in stats['features'].keys():
            fun = getattr(self, 'get_%s' % feature, None)

            if callable(fun):
                stat = EntityStat()
                stat.feature = feature
                stat.time = timestamp
                stat.federation = self
                stat.value = fun(entities, stats['features'][feature])
                entity_stats.append(stat)

        EntityStat.objects.bulk_create(entity_stats)

    def process_metadata_entities(self, request=None, federation_slug=None, timestamp=timezone.now()):
        entities_from_xml = self._metadata.get_entities()
        removed = self._remove_deleted_entities(entities_from_xml, request)

        entities = {}
        db_entities = Entity.objects.filter(entityid__in=entities_from_xml)
        db_entities = db_entities.prefetch_related('types')

        for entity in db_entities.all():
            entities[entity.entityid] = entity

        if request and federation_slug:
            request.session['%s_num_entities' % federation_slug] = len(entities_from_xml)
            request.session['%s_cur_entities' % federation_slug] = 0
            request.session['%s_process_done' % federation_slug] = False
            request.session.save()

        updated = self._add_new_entities(entities, entities_from_xml, request, federation_slug)

        if request and federation_slug:
            request.session['%s_process_done' % federation_slug] = True
            request.session.save()

        return removed, updated

    def get_absolute_url(self):
        return reverse('federation_view', args=[self.slug])

    @classmethod
    def get_sp(cls, entities, xml_name):
        count = 0
        for entity in entities:
            cur_cached_types = [t.xmlname for t in entity.types.all()]
            if xml_name in cur_cached_types:
                count += 1
        return count

    @classmethod
    def get_idp(cls, entities, xml_name):
        count = 0
        for entity in entities:
            cur_cached_types = [t.xmlname for t in entity.types.all()]
            if xml_name in cur_cached_types:
                count += 1
        return count

    def get_sp_saml1(self, entities, xml_name):
        return self.get_stat_protocol(entities, xml_name, 'SPSSODescriptor')

    def get_sp_saml2(self, entities, xml_name):
        return self.get_stat_protocol(entities, xml_name, 'SPSSODescriptor')

    def get_sp_shib1(self, entities, xml_name):
        return self.get_stat_protocol(entities, xml_name, 'SPSSODescriptor')

    def get_idp_saml1(self, entities, xml_name):
        return self.get_stat_protocol(entities, xml_name, 'IDPSSODescriptor')

    def get_idp_saml2(self, entities, xml_name):
        return self.get_stat_protocol(entities, xml_name, 'IDPSSODescriptor')

    def get_idp_shib1(self, entities, xml_name):
        return self.get_stat_protocol(entities, xml_name, 'IDPSSODescriptor')

    def get_stat_protocol(self, entities, xml_name, service_type):
        count = 0
        for entity in entities:
            try:
                cur_cached_types = [t.xmlname for t in entity.types.all()]
                if service_type in cur_cached_types and Entity.READABLE_PROTOCOLS[xml_name] in entity.display_protocols():
                    count += 1
            except Exception, e:
                pass
        return count

    def can_edit(self, user, delete):
        if user.is_superuser:
            return True

        permission = 'delete_federation' if delete else 'change_federation'
        if user.has_perm('metadataparser.%s' % permission) and user in self.editor_users.all():
            return True
        return False


class EntityQuerySet(QuerySet):
    def iterator(self):
        cached_federations = {}
        for entity in super(EntityQuerySet, self).iterator():
            if entity.file:
                continue

            federations = entity.federations.all()
            if federations:
                federation = federations[0]
            else:
                raise ValueError("Can't find entity metadata")

            for federation in federations:
                if not federation.id in cached_federations:
                    cached_federations[federation.id] = federation

                cached_federation = cached_federations[federation.id]
                try:
                    entity.load_metadata(federation=cached_federation)
                except ValueError:
                    # Allow entity in federation but not in federation file
                    continue
                else:
                    break

            yield entity


class EntityManager(models.Manager):
    def get_queryset(self):
        return EntityQuerySet(self.model, using=self._db)


class EntityType(models.Model):
    name = models.CharField(blank=False, max_length=20, unique=True,
                            verbose_name=_(u'Name'), db_index=True)
    xmlname = models.CharField(blank=False, max_length=20, unique=True,
                            verbose_name=_(u'Name in XML'), db_index=True)

    def __unicode__(self):
        return self.name


class Entity(Base):
    READABLE_PROTOCOLS = {
        'urn:oasis:names:tc:SAML:1.1:protocol': 'SAML 1.1',
        'urn:oasis:names:tc:SAML:2.0:protocol': 'SAML 2.0',
        'urn:mace:shibboleth:1.0': 'Shiboleth 1.0',
    }

    entityid = models.CharField(blank=False, max_length=200, unique=True,
                                verbose_name=_(u'EntityID'), db_index=True)
    federations = models.ManyToManyField(Federation,
                                         verbose_name=_(u'Federations'))

    types = models.ManyToManyField(EntityType, verbose_name=_(u'Type'))

    name = JSONField(blank=True, null=True, max_length=2000,
                     verbose_name=_(u'Display Name'))

    objects = models.Manager()
    longlist = EntityManager()

    curfed = None

    @property
    def registration_authority_xml(self):
        return self._get_property('registration_authority')

    @property
    def registration_policy(self):
        return self._get_property('registration_policy')

    @property
    def registration_instant(self):
        reginstant = self._get_property('registration_instant')
        if reginstant is None:
            return None
        reginstant = "%sZ" % reginstant[0:19]
        return datetime.strptime(reginstant, '%Y-%m-%dT%H:%M:%SZ')

    @property
    def protocols(self):
        return ' '.join(self._get_property('protocols'))

    @property
    def languages(self):
        return ' '.join(self._get_property('languages'))

    @property
    def scopes(self):
        return ' '.join(self._get_property('scopes'))

    @property
    def attributes(self):
        attributes = self._get_property('attr_requested')
        if not attributes:
            return []
        return attributes['required']

    @property
    def attributes_optional(self):
        attributes = self._get_property('attr_requested')
        if not attributes:
            return []
        return attributes['optional']

    @property
    def organization(self):
        organization = self._get_property('organization')
        if not organization:
            return []

        vals = []
        for lang, data in organization.items():
            data['lang'] = lang
            vals.append(data)

        return vals

    @property
    def display_name(self):
        return self._get_property('displayName')

    @property
    def federations_count(self):
        return str(self.federations.all().count())
        
    @property
    def description(self):
        return self._get_property('description')

    @property
    def info_url(self):
        return self._get_property('infoUrl')

    @property
    def privacy_url(self):
        return self._get_property('privacyUrl')

    @property
    def xml(self):
         return self._get_property('xml')

    @property
    def xml_types(self):
         return self._get_property('entity_types')

    @property
    def display_protocols(self):
        protocols = []

        xml_protocols = self._get_property('protocols')
        if xml_protocols:
            for proto in xml_protocols:
                protocols.append(self.READABLE_PROTOCOLS.get(proto, proto))

        return protocols

    def display_attributes(self):
        attributes = {}
        for [attr, friendly] in self.attributes:
            if friendly:
                attributes[attr] = friendly
            elif attr in attributemap.MAP['fro']:
                attributes[attr] = attributemap.MAP['fro'][attr]
            else:
                attributes[attr] = '?'
        return attributes

    def display_attributes_optional(self):
        attributes = {}
        for [attr, friendly] in self.attributes_optional:
            if friendly:
                attributes[attr] = friendly
            elif attr in attributemap.MAP['fro']:
                attributes[attr] = attributemap.MAP['fro'][attr]
            else:
                attributes[attr] = '?'
        return attributes

    @property
    def contacts(self):
        contacts = []
        for cur_contact in self._get_property('contacts'):
            if cur_contact['name'] and cur_contact['surname']:
                contact_name = '%s %s' % (cur_contact['name'], cur_contact['surname'])
            elif cur_contact['name']:
                contact_name = cur_contact['name']
            elif cur_contact['surname']:
                contact_name = cur_contact['surname']
            else:
                contact_name = urlparse(cur_contact['email']).path.partition('?')[0]
            c_type = 'undefined'
            if cur_contact['type']:
                c_type = cur_contact['type']
            contacts.append({ 'name': contact_name, 'email': cur_contact['email'], 'type': c_type })
        return contacts

    @property
    def logos(self):
        logos = []
        for cur_logo in self._get_property('logos'):
            cur_logo['external'] = True
            logos.append(cur_logo)

        return logos

    class Meta:
        verbose_name = _(u'Entity')
        verbose_name_plural = _(u'Entities')

    def __unicode__(self):
        return self.entityid

    def load_metadata(self, federation=None, entity_data=None):
        if hasattr(self, '_entity_cached'):
            return

        if self.file:
            self._entity_cached = self.load_file().get_entity(self.entityid)
        elif federation:
            self._entity_cached = federation.get_entity_metadata(self.entityid)
        elif entity_data:
            self._entity_cached = entity_data
        else:
            right_fed = None
            first_fed = None
            for fed in self.federations.all():
                if fed.registration_authority == self.registration_authority:
                    right_fed = fed
                if first_fed is None:
                    first_fed = fed

            if right_fed is not None:
                entity_cached = right_fed.get_entity_metadata(self.entityid)
                self._entity_cached = entity_cached
            else:
                entity_cached = first_fed.get_entity_metadata(self.entityid)
                self._entity_cached = entity_cached

        if not hasattr(self, '_entity_cached'):
            raise ValueError("Can't find entity metadata")

    def _get_property(self, prop, federation=None):
        try:
            self.load_metadata(federation or self.curfed)
        except ValueError:
            return None

        if hasattr(self, '_entity_cached'):
            return self._entity_cached.get(prop, None)
        else:
            raise ValueError("Not metadata loaded")

    def _get_or_create_etypes(self, cached_entity_types):
        entity_types = []
        cur_cached_types = [t.xmlname for t in self.types.all()]
        for etype in self.xml_types:
            if etype in cur_cached_types:
               break

            if cached_entity_types is None:
                entity_type, created = EntityType.objects.get_or_create(xmlname=etype,
                                                                        name=DESCRIPTOR_TYPES_DISPLAY[etype])
            else:
                if etype in cached_entity_types:
                    entity_type = cached_entity_types[etype]
                else:
                    entity_type = EntityType.objects.create(xmlname=etype,
                                                                name=DESCRIPTOR_TYPES_DISPLAY[etype])
            entity_types.append(entity_type)
        return entity_types

    def process_metadata(self, auto_save=True, entity_data=None, cached_entity_types=None):
        if not entity_data:
            self.load_metadata()

        if self.entityid != entity_data.get('entityid'):
            raise ValueError("EntityID is not the same")

        self._entity_cached = entity_data
        if self.xml_types:
            entity_types = self._get_or_create_etypes(cached_entity_types)

            if len(entity_types) > 0:
                self.types.add(*entity_types)

            newname = self._get_property('displayName')
            if newname and newname != '':
                self.name = newname

            if str(self._get_property('registration_authority')) != '':
                self.registration_authority = self._get_property('registration_authority')

            if auto_save:
                self.save()

    def to_dict(self):
        self.load_metadata()

        entity = self._entity_cached.copy()
        entity["types"] = [unicode(f) for f in self.types.all()]
        entity["federations"] = [{u"name": unicode(f), u"url": f.get_absolute_url()}
                                  for f in self.federations.all()]

        if self.registration_authority:
            entity["registration_authority"] = self.registration_authority
        if self.registration_instant:
            entity["registration_instant"] = '%s' % self.registration_instant

        if "file_id" in entity.keys():
            del entity["file_id"]
        if "entity_types" in entity.keys():
            del entity["entity_types"]

        return entity

    def display_etype(value, separator=', '):
            return separator.join([unicode(item) for item in value.all()])

    @classmethod
    def get_most_federated_entities(self, maxlength=TOP_LENGTH, cache_expire=None):
        entities = None
        if cache_expire:
            cache = get_cache("default")
            entities = cache.get("most_federated_entities")

        if not entities or len(entities) < maxlength:
            # Entities with count how many federations belongs to, and sorted by most first
            ob_entities = Entity.objects.all().annotate(federationslength=Count("federations")).order_by("-federationslength")
            ob_entities = ob_entities.prefetch_related('types', 'federations')
            ob_entities = ob_entities[:maxlength]

            entities = []
            for entity in ob_entities:
                entities.append({
                    'entityid': entity.entityid,
                    'name': entity.name,
                    'absolute_url': entity.get_absolute_url(),
                    'types': [unicode(item) for item in entity.types.all()],
                    'federations': [(unicode(item.name), item.get_absolute_url()) for item in entity.federations.all()],
                })

        if cache_expire:
            cache = get_cache("default")
            cache.set("most_federated_entities", entities, cache_expire)

        return entities[:maxlength]

    def get_absolute_url(self):
        return reverse('entity_view', args=[quote_plus(self.entityid)])

    def can_edit(self, user, delete):
        permission = 'delete_entity' if delete else 'change_entity'
        if user.is_superuser or (user.has_perm('metadataparser.%s' % permission) and user in self.editor_users.all()):
            return True

        for federation in self.federations.all():
            if federation.can_edit(user, False):
                return True

        return False

class EntityStat(models.Model):
    time = models.DateTimeField(blank=False, null=False, 
                           verbose_name=_(u'Metadata time stamp'))
    feature = models.CharField(max_length=100, blank=False, null=False, db_index=True,
                           verbose_name=_(u'Feature name'))

    value = models.PositiveIntegerField(max_length=100, blank=False, null=False,
                           verbose_name=_(u'Feature value'))

    federation = models.ForeignKey(Federation, blank = False,
                                         verbose_name=_(u'Federations'))

    def __unicode__(self):
        return self.feature


class Dummy(models.Model):
    pass


@receiver(pre_save, sender=Federation, dispatch_uid='federation_pre_save')
def federation_pre_save(sender, instance, **kwargs):
    # Skip pre_save if only file name is saved 
    if kwargs.has_key('update_fields') and kwargs['update_fields'] == set(['file']):
        return

    slug = slugify(unicode(instance.name))[:200]
    if instance.file_url:
        instance.fetch_metadata_file(slug)
    if instance.name:
        instance.slug = slugify(unicode(instance))[:200]


@receiver(pre_save, sender=Entity, dispatch_uid='entity_pre_save')
def entity_pre_save(sender, instance, **kwargs):
    if instance.file_url:
        slug = slugify(unicode(instance.name))[:200]
        instance.fetch_metadata_file(slug)
        instance.process_metadata()
