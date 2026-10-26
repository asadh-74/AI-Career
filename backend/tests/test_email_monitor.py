import sys
from pathlib import Path
from types import SimpleNamespace as NS
sys.path.insert(0,str(Path(__file__).parents[1]))
from email_monitor import match_application, classify_message

def pair(company='Binance',title='Pioneer Talent Program Full Stack AI Engineer Backend Oriented Top Talent Program'):
    return (NS(id=1),NS(company=company,title=title))

def test_freelancer_digest_never_matches():
    msg={'sender':'Freelancer <noreply@notifications.freelancer.com>', 'subject':'Asad, these Research, Data Entry, and Excel projects might interest you', 'body':'Binance assessment Pioneer Talent Program'}
    assert match_application(msg,[pair()]) is None
    assert classify_message(msg['subject'],msg['body']) == ('job_recommendation',False)

def test_generic_role_overlap_is_not_company_evidence():
    assert match_application({'body':'Pioneer Talent Program assessment'},[pair()]) is None

def test_company_substring_does_not_match():
    assert match_application({'body':'interview about capital'},[pair('Cap')]) is None

def test_real_company_response_matches():
    p=pair()
    assert match_application({'sender':'recruiter@binance.com','subject':'Your coding test'},[p]) == p

def test_ambiguous_roles_need_review():
    assert match_application({'subject':'Binance application update'},[pair(),pair(title='Python developer')]) is None

def test_list_header_overrides_company_match():
    assert match_application({'subject':'Binance interview','bulk':True},[pair()]) is None

def test_offer_keyword_not_enough():
    assert classify_message('Special offer','discount')[0] != 'offer'
