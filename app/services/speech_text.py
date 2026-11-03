# SPDX-FileCopyrightText: 2026 Ahmet Erol Bayrak
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Retained deterministic speech preparation, without a model rewrite pass."""
import re
from xml.sax.saxutils import escape,quoteattr

MAX_CHARS=3000


def strip_for_speech(text,locale='tr-TR'):
    code,table=('kod atlandı','tablo atlandı') if locale.lower().startswith('tr') else ('code omitted','table omitted')
    out=re.sub(r'```[\s\S]*?```',' '+code+' ',text)
    out=re.sub(r'(?:^[ \t]*\|.*\|[ \t]*$\n?)+',' '+table+' ',out,flags=re.M)
    out=re.sub(r'`([^`]*)`',r'\1',out)
    out=re.sub(r'!\[[^\]]*\]\([^)]*\)',' ',out)
    out=re.sub(r'\[([^\]]+)\]\([^)]*\)',r'\1',out)
    out=re.sub(r'^\s{0,3}#{1,6}\s*','',out,flags=re.M)
    out=re.sub(r'^\s{0,3}>\s?','',out,flags=re.M)
    out=re.sub(r'^\s*[-*+]\s+','',out,flags=re.M)
    out=re.sub(r'[*_]{1,3}([^*_]+)[*_]{1,3}',r'\1',out)
    return re.sub(r'\n{3,}','\n\n',re.sub(r'[ \t]{2,}',' ',out)).strip()


def build_ssml(text,voice,locale):
    return f'<speak version="1.0" xml:lang={quoteattr(locale)}><voice name={quoteattr(voice)}>{escape(text)}</voice></speak>'


def parse_voice_ref(ref):
    parts=ref.split(':')
    if parts[0] in {'openai','elevenlabs'} and len(parts)==3: return tuple(parts)
    if parts[0]=='azure' and len(parts)==2: return 'azure','neural',parts[1]
    if parts[0]=='azure' and len(parts)==3 and parts[1]=='neural': return tuple(parts)
    if len(parts)==1: return 'azure','neural',ref
    raise ValueError('Use provider:model:voice, or an Azure voice name')
