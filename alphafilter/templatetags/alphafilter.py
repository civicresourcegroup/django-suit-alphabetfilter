from __future__ import absolute_import
import django
from django.utils.translation import ugettext as _
from django.template import (Library, Node, Variable, VariableDoesNotExist,
                             TemplateSyntaxError, RequestContext, Context)
from django.template.loader import get_template

from alphafilter.sql import FirstLetter
from django.db import models
register = Library()

from django.conf import settings
ALPHABET_FILTER_BY_FIELD = getattr(settings, 'ALPHABET_FILTER_BY_FIELD', ('name', 'title'))


def _get_default_letters(model_admin=None):
    """
    Returns the set of letters defined in the configuration variable
    DEFAULT_ALPHABET. DEFAULT_ALPHABET can be a callable, string, tuple, or
    list and returns a set.

    If a ModelAdmin class is passed, it will look for a DEFAULT_ALPHABET
    attribute and use it instead.
    """
    from django.conf import settings
    import string
    default_ltrs = string.digits + string.ascii_uppercase
    default_letters = getattr(settings, 'DEFAULT_ALPHABET', default_ltrs)
    if model_admin and hasattr(model_admin, 'DEFAULT_ALPHABET'):
        default_letters = model_admin.DEFAULT_ALPHABET
    if callable(default_letters):
        return set(default_letters())
    elif isinstance(default_letters, unicode):
        return set([x for x in default_letters])
    elif isinstance(default_letters, str):
        return set([x for x in default_letters.decode('utf8')])
    elif isinstance(default_letters, (tuple, list)):
        return set(default_letters)


def _get_available_letters(field_name, queryset):
    """
    Makes a query to the database to return the first character of each
    value of the field and table passed in.

    Returns a set that represents the letters that exist in the database.
    """
    def upper_first_letter(field_name, element):

        def filter_func(element):
            if hasattr(element, field_name):
                return getattr(element, field_name)
            else:
                set_fields = set(element.__dict__.keys())
                intersection_name = list(set_fields.intersection(ALPHABET_FILTER_BY_FIELD))
                return getattr(element, intersection_name[0]) if intersection_name else \
                    element.pk

        word = filter_func(element)
        if word and isinstance(word, basestring):
            return word[0].upper()
        elif word and isinstance(word, int):
            return unicode(word)[0]

    if django.VERSION[1] <= 4:
        result = queryset.values(field_name).annotate(
            fl=FirstLetter(field_name)
        ).values('fl').distinct()
        return set([res['fl'] for res in result if res['fl'] is not None])
    else:
        return set([upper_first_letter(field_name, row)
                   for row in queryset
                   if row is not None])


def alphabet(cl):
    """
    The inclusion tag that renders the admin/alphabet.html template in the
    admin. Accepts a ChangeList object, which is custom to the admin.
    """
    if not getattr(cl.model_admin, 'alphabet_filter', False):
        return
    field_name = cl.model_admin.alphabet_filter
    alpha_field = '%s__istartswith' % field_name
    alpha_lookup = cl.params.get(alpha_field, '')
    link = lambda d: cl.get_query_string(d)
    
    qs = cl.model.objects.all()
    
    ########## filter out soft-deleted objects
    try: 
      cl.model._meta.get_field_by_name('is_deleted') 
    except models.FieldDoesNotExist:
      pass
    else:
      qs = qs.filter(is_deleted=False)
    ###########################################  
    
    letters_used = _get_available_letters(field_name, qs)
    all_letters = list(_get_default_letters(cl.model_admin) | letters_used)
    all_letters.sort()

    choices = [{
        'link': link({alpha_field: letter}),
        'title': letter,
        'active': letter == alpha_lookup,
        'has_entries': letter in letters_used, } for letter in all_letters]
    all_letters = [{
        'link': cl.get_query_string(None, [alpha_field]),
        'title': _('All'),
        'active': '' == alpha_lookup,
        'has_entries': True
    }, ]
    return {'choices': all_letters + choices}
alphabet = register.inclusion_tag('admin/alphabet.html')(alphabet)


class AlphabetFilterNode(Node):
    """
    Provide a list of links for first characters on items in a queryset

    {% qs_alphabet_filter objects "lastname" "myapp/template.html" %}
    """
    def __init__(self, qset, field_name, filtered=None,
                 template_name="alphafilter/alphabet.html", strip_params=None):
        self.qset = Variable(qset)
        self.field_name = Variable(field_name)
        self.template_name = Variable(template_name)
        self.filtered = filtered
        if strip_params is None:
            self.strip_params = []
        else:
            self.strip_params = strip_params.split(',')

    def render(self, context):
        try:
            qset = self.qset.resolve(context)
        except VariableDoesNotExist:
            raise TemplateSyntaxError("Can't resolve the queryset passed")
        try:
            field_name = self.field_name.resolve(context)
        except VariableDoesNotExist:
            field_name = self.field_name.var

        if not field_name:
            return ''

        alpha_field = '%s__istartswith' % field_name
        request = context.get('request', None)

        if request is not None:
            alpha_lookup = request.GET.get(alpha_field, '')
            qstring_items = request.GET.copy()
            self.strip_params.append(alpha_field)
            self.strip_params.append('page')
            for param in self.strip_params:
                if param in qstring_items:
                    qstring_items.pop(param)
            qstring = "&".join(["%s=%s" % (k, v) for k, v in qstring_items.iteritems()])
        else:
            alpha_lookup = ''
            qstring = ''

        link = lambda d: "?%s&%s" % (qstring, "%s=%s" % d.items()[0])
        if self.filtered is None:
            letters_used = _get_available_letters(field_name, qset)
        else:
            letters = [getattr(row, field_name)[0] for row in qset]
            if alpha_lookup == '' and letters is not None:
                alpha_lookup = letters[0]
            letters_used = set(letters)

        all_letters = list(_get_default_letters(None) | letters_used)
        all_letters.sort()

        choices = [{
            'link': link({alpha_field: letter}),
            'title': letter,
            'active': letter == alpha_lookup,
            'has_entries': letter in letters_used, } for letter in all_letters]
        all_letters = [{
            'link': link({alpha_field: ''}),
            'title': _('All'),
            'active': '' == alpha_lookup,
            'has_entries': True
        }, ]
        ctxt = {'choices': all_letters + choices}

        tmpl = get_template(self.template_name)

        if request is not None:
            return tmpl.render(RequestContext(request, ctxt))
        else:
            return tmpl.render(Context(ctxt))


@register.tag
def qs_alphabet_filter(parser, token):
    """
    The parser/tokenizer for the queryset alphabet filter.

    {% qs_alphabet_filter <queryset> <field name> [<template name>] [strip_params=comma,delim,list] %}

    {% qs_alphabet_filter objects lastname myapp/template.html %}

    The template name is optional and uses alphafilter/alphabet.html if not
    specified
    """
    bits = token.split_contents()
    if len(bits) == 3:
        return AlphabetFilterNode(bits[1], bits[2])
    elif len(bits) == 4:
        if "=" in bits[3]:
            key, val = bits[3].split('=')
            return AlphabetFilterNode(bits[1], bits[2], strip_params=val)
        else:
            return AlphabetFilterNode(bits[1], bits[2], template_name=bits[3])
    elif len(bits) == 5:
        key, val = bits[4].split('=')
        return AlphabetFilterNode(bits[1], bits[2], bits[3], bits[4])
    else:
        raise TemplateSyntaxError("%s is called with a queryset and field "
                                  "name, and optionally a template." % bits[0])
