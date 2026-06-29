import os, subprocess

password = 'hardcoded_secret_123'

def bad():
    eval("dangerous")
    exec("more dangerous")
    os.system("rm -rf /")
    subprocess.run("echo hi", shell=True)
    subprocess.call(["ls"], shell=True)
    f = open("secret.txt")
