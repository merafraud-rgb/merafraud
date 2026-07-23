"""
MeraFraud - Disposable Email Detection
--------------------------------------------
"Uses gmail" is a weak signal (half the internet does). "Uses a 10-minute
throwaway inbox" is a strong fraud signal. This module separates the two
instead of lumping them together, which is what FraudLabsPro-style tools
do with their live disposable-email databases.

This is a static list (~150 common disposable-email domains) rather than
a live-updated database — good enough for an MVP, but new disposable
services appear constantly. For production-grade coverage, consider a
paid service (e.g. MailboxValidator, Abstract API) — swap this list-based
check for an API call the same way ip_intelligence.py could be upgraded.
"""

DISPOSABLE_EMAIL_DOMAINS = {
    "mailinator.com", "guerrillamail.com", "guerrillamail.info", "10minutemail.com",
    "10minutemail.net", "tempmail.com", "temp-mail.org", "yopmail.com", "yopmail.net",
    "throwawaymail.com", "trashmail.com", "trashmail.net", "sharklasers.com",
    "getnada.com", "mailnesia.com", "mintemail.com", "mytemp.email", "moakt.com",
    "dispostable.com", "fakeinbox.com", "spamgourmet.com", "mailcatch.com",
    "mailexpire.com", "emailondeck.com", "mohmal.com", "tempinbox.com",
    "tempail.com", "tempmailo.com", "temp-mail.io", "throwam.com", "burnermail.io",
    "maildrop.cc", "inboxbear.com", "mailsac.com", "fakemail.net", "spambog.com",
    "grr.la", "guerrillamailblock.com", "pokemail.net", "spamfree24.org",
    "tmpeml.com", "tmpmail.net", "tmpmail.org", "tmail.ws", "tmails.net",
    "emailfake.com", "email-fake.com", "fake-mail.net", "20minutemail.com",
    "33mail.com", "anonbox.net", "deadaddress.com", "despam.it", "e4ward.com",
    "einrot.com", "explodemail.com", "fakemailgenerator.com", "harakirimail.com",
    "incognitomail.com", "jetable.org", "kasmail.com", "klzlk.com", "kurzepost.de",
    "lifebyfood.com", "meltmail.com", "messagebeamer.de", "mytrashmail.com",
    "no-spam.ws", "nobulk.com", "noclickemail.com", "nogmailspam.info",
    "nomail2me.com", "nospam4.us", "nospamfor.us", "objectmail.com", "obobbo.com",
    "onewaymail.com", "pancakemail.com", "pooae.com", "privacy.net", "punkass.com",
    "putthisinyourspamdatabase.com", "quickinbox.com", "rcpt.at", "recode.me",
    "recyclemail.dk", "regbypass.com", "safe-mail.net", "sneakemail.com",
    "sofimail.com", "sogetthis.com", "soodonims.com", "spam.la", "spam4.me",
    "spamavert.com", "spambob.com", "spambob.net", "spambob.org", "spamcannon.com",
    "spamcero.com", "spamcon.org", "spamcorptastic.com", "spamday.com",
    "spamex.com", "spamherelots.com", "spamhereplease.com", "spamhole.com",
    "spamify.com", "spaminator.de", "spamkill.info", "spaml.com", "spaml.de",
    "spammotel.com", "spamobox.com", "spamoff.de", "spamslicer.com",
    "spamspot.com", "spamthis.co.uk", "spamthisplease.com", "spamtroll.net",
    "speed.1s.fr", "supergreatmail.com", "supermailer.jp", "suremail.info",
    "thankyou2010.com", "thisisnotmyrealemail.com", "throwawayemailaddress.com",
    "tilien.com", "tmail.com", "tmailinator.com", "toiea.com", "trash2009.com",
    "trash-amil.com", "trashdevil.com", "trashemail.de", "trashymail.com",
    "trbvm.com", "veryrealemail.com", "wegwerfemail.de", "wh4f.org",
    "whyspam.me", "willselfdestruct.com", "winemaven.info", "wuzup.net",
    "wuzupmail.net", "xagloo.com", "yeah.net", "zoemail.org",
}


def is_disposable(email_or_domain: str) -> bool:
    if not email_or_domain:
        return False
    domain = email_or_domain.strip().lower()
    if "@" in domain:
        domain = domain.split("@")[-1]
    return domain in DISPOSABLE_EMAIL_DOMAINS
