"""SEC EDGAR gets a declared contact; no other host ever receives it."""
import pytest
from pydantic import ValidationError

from briefloop.models import Settings
from briefloop.sources import _agent, _sec_hint


def test_contact_is_sent_only_to_sec_and_missing_contact_explains_403():
    assert _agent('www.sec.gov', 'r@example.org').endswith(' r@example.org')
    assert 'r@example.org' not in _agent('investors.example.com', 'r@example.org')
    assert 'r@example.org' not in _agent('notsec.gov', 'r@example.org')
    assert '联系邮箱' in _sec_hint('www.sec.gov', '', 'curl: (56) The requested URL returned error: 403')
    assert _sec_hint('www.sec.gov', 'r@example.org', 'error: 403') == 'error: 403'
    assert Settings(fetch_contact_email='').fetch_contact_email == ''
    with pytest.raises(ValidationError):
        Settings(fetch_contact_email='not-an-email')
