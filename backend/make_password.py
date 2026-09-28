from getpass import getpass
from passlib.hash import pbkdf2_sha256
password = getpass('Choose a long dashboard password: ')
if len(password) < 12: raise SystemExit('Use at least 12 characters')
print(pbkdf2_sha256.hash(password))
