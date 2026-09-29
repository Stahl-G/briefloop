"""A native host failure cannot discard an already admitted Word template."""
from hashlib import sha256
from io import BytesIO
import json
import threading
from types import SimpleNamespace

from docx import Document
import pytest

from briefloop.export_jobs import enqueue_export, generate_word, output_path
from briefloop.native_orchestrator import template_submit
from briefloop.runtime import Worker
from briefloop.store import Store, dump
from briefloop.templates import import_template, prepare, template


def template_job(tmp_path):
    store = Store(tmp_path)
    original = Document()
    original.add_paragraph('Monthly Report')
    original.add_heading('Summary', level=1)
    original.add_paragraph('Old body text.')
    buffer = BytesIO()
    original.save(buffer)
    record = import_template(store, 'Monthly.docx', buffer.getvalue(), prepare_job=False)
    job = store.enqueue('prepare_template', {'template_id': record['id']})
    store.update_job(job['id'], 'running')
    return store, record, store.one('jobs', job['id'])


def template_spec():
    return {
        'keep_blocks': [0], 'paragraph_index': 2,
        'sections': [{'section_id': 'summary', 'title': 'Summary', 'index': 1}],
        'fields': [{'old': 'Monthly Report', 'field': 'title'}],
    }


def save_template(store, record):
    return prepare(store, record['id'], template_spec())


class Host:
    def __init__(self, action):
        self.action = action
        self.cancelled = threading.Event()

    def execute(self, *args, **kwargs):
        self.action()
        raise RuntimeError('host disconnected after the template turn')

    def cancel(self):
        self.cancelled.set()
