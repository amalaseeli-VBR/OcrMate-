import base64, os

key = base64.urlsafe_b64encode(os.urandom(32)).decode()
print(key)