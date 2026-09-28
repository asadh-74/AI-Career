from getpass import getpass
from pathlib import Path
import secrets
from passlib.hash import pbkdf2_sha256
path=Path('.env')
if path.exists(): raise SystemExit('.env already exists; keeping your current settings')
pw=getpass('Choose a dashboard password (12+ characters): ')
if len(pw)<12: raise SystemExit('Password too short')
path.write_text('ADMIN_PASSWORD_HASH='+pbkdf2_sha256.hash(pw)+'\nSESSION_SECRET='+secrets.token_urlsafe(48)+'\nGEMINI_API_KEY=\nCORS_ORIGINS=http://localhost:3000,http://127.0.0.1:3000\n',encoding='utf-8')
print('Created .env. Add a new GEMINI_API_KEY to enable AI matching.')
