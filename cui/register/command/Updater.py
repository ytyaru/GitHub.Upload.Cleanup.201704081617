#!python3
#encoding:utf-8
import os.path
import subprocess
import shlex
import re
import datetime
import traceback
import copy
import dataset
import database.src.Database
import web.service.github.api.v3.CurrentUser
import cui.register.github.api.v3.authorizations.Authorizations
import cui.register.github.api.v3.users.SshKeys
import cui.register.github.api.v3.users.Emails
import cui.register.github.api.v3.users.Users
import web.sqlite.Json2Sqlite
import cui.register.SshConfigurator
class Updater:
    def __init__(self):
        self.__j2s = web.sqlite.Json2Sqlite.Json2Sqlite()
        self.__db = None

    def Update(self, args):
        print('Account.Update')
        print(args)
        print('-u: {0}'.format(args.username))
        print('-rn: {0}'.format(args.rename))
        print('-p: {0}'.format(args.password))
        print('-m: {0}'.format(args.mailaddress))
        print('-s: {0}'.format(args.ssh_host))
        print('-t: {0}'.format(args.two_factor_secret_key))
        print('-r: {0}'.format(args.two_factor_recovery_code_file_path))
        print('--auto: {0}'.format(args.auto))

        self.__db = database.src.Database.Database()
        self.__db.Initialize()
        
        account = self.__db.account['Accounts'].find_one(Username=args.username)
        print(account)
        
        if None is account:
            print('指定したユーザ {0} がDBに存在しません。更新を中止します。'.format(args.username))
            return
        
        # Accountsテーブルを更新する（ユーザ名、パスワード、メールアドレス）
        self.__UpdateAccounts(args, account)
        
        # SSH鍵を更新する(APIの削除と新規作成で。ローカルで更新し~/.ssh/configで設定済みとする)
        self.__UpdateSsh(args, account)
        
        # 未実装は以下。
        # E. 2FA-Secret
        # F. 2FA-Recovery-Code
        
        # 作成したアカウントのリポジトリDB作成や、作成にTokenが必要なライセンスDBの作成
        self.__db.Initialize()
        return self.__db

    def __UpdateAccounts(self, args, account):
        new_account = copy.deepcopy(account)
        # ユーザ名とパスワードを変更する
        if None is not args.rename or None is not args.password:
            j_user = self.__IsValidUsernameAndPassword(args, account)
            if None is not args.rename:
                new_account['Username'] = args.rename
            if None is not args.password:
                new_account['Password'] = args.password
            new_account['CreatedAt'] = j_user['created_at']
            new_account['UpdatedAt'] = j_user['updated_at']
        # メールアドレスを更新する
        if args.mailaddress:
            user = web.service.github.api.v3.CurrentUser.CurrentUser(self.__db, account['Username'])
            token = user.GetAccessToken(scopes=['user', 'user:email'])
            mail = self.__GetPrimaryMail(token)
            if mail != account['MailAddress']:
                new_account['MailAddress'] = self.__GetPrimaryMail(token)
            else:
                print('MailAddressはDBと同一でした。: {0}'.format(mail))
        # DBを更新する
        self.__db.account['Accounts'].update(new_account, ['Id'])
    
    def __IsValidUsernameAndPassword(self, args, account):
        if None is args.password:
            password = account['Password']
        else:
            password = args.password
        print('password: ' + password)
        users = cui.register.github.api.v3.users.Users.Users(args.username, password)
        try:
            j = users.Get()
            account['CreatedAt'] = j['created_at']
        except:
            raise Exception('指定したユーザ名とパスワードでAPI実行しましたがエラーです。有効なユーザ名とパスワードではない可能性があります。')
            return None
        return j

    def __GetPrimaryMail(self, token):
        emails = cui.register.github.api.v3.users.Emails.Emails()
        mails = emails.Gets(token)
        print(mails)
        for mail in mails:
            if mail['primary']:
                return mail['email']

    def __UpdateSsh(self, args, account):
        if None is args.ssh_host:
            return
        sshconf = cui.register.SshConfigurator.SshConfigurator()
        sshconf.Load()
        if not(args.ssh_host in sshconf.Hosts):
            raise Exception('指定したSSHホスト名 {0} は~/.ssh/config内に未定義です。定義してから再度実行してください。')
        if 1 < self.__db.account['AccessTokens'].count(Username=account['Username']):
            raise Exception('プログラムエラー。1ユーザ1Tokenのはずですが、Tokenが2つ以上あります。')
        
        # GitHubAPIでSSH鍵を削除する
        user = web.service.github.api.v3.CurrentUser.CurrentUser(self.__db, account['Username'])
        token = user.GetAccessToken(scopes=['admin:public_key'])
        token = self.__db.account['AccessTokens'].find_one(AccountId=account['Id'])
        print(token)
        api_ssh = cui.register.github.api.v3.users.SshKeys.SshKeys()
        print(api_ssh)
        
        if None is args.password:
            password = account['Password']
        else:
            password = args.password
        api_ssh.Delete(token['SshKeyId'], args.username, password)
        
        # GitHubAPIでSSH鍵を生成する
        ssh_key_gen_params = self.__LoadSshKeyFile(args, sshconf)
        j_ssh = api_ssh.Create(token['AccessToken'], account['MailAddress'], ssh_key_gen_params['public_key'])
        print(j_ssh)
        
        # SSH接続確認
        self.__SshConnectCheck(args.ssh_host, sshconf.Hosts[args.ssh_host]['User'], ssh_key_gen_params['path_file_key_private'])
        
        # DB更新
        if 1 < self.__db.account['AccessTokens'].count(AccountId=account['Id']):
            raise Exception('プログラムエラー。1ユーザ1Tokenのはずですが、Tokenが2つ以上あります。')
        if 1 < self.__db.account['SshConfigures'].count(AccountId=account['Id']):
            raise Exception('プログラムエラー。1ユーザ1SshConfiguresレコードのはずですが、レコードが2つ以上あります。')
        if 1 < self.__db.account['SshKeys'].count(AccountId=account['Id']):
            raise Exception('プログラムエラー。1ユーザ1SshKeysレコードのはずですが、レコードが2つ以上あります。')
        rec_token = self.__db.account['AccessTokens'].find_one(AccountId=account['Id'])
        rec_token['SshKeyId'] = j_ssh['id']
        self.__db.account['AccessTokens'].update(rec_token, ['Id'])
            
        sshconfigures = self.__db.account['SshConfigures'].find_one(AccountId=account['Id'])
        sshconfigures['HostName'] = args.ssh_host
        sshconfigures['PrivateKeyFilePath'] = ssh_key_gen_params['path_file_key_private']
        sshconfigures['PublicKeyFilePath'] = ssh_key_gen_params['path_file_key_public']
        sshconfigures['Type'] = ssh_key_gen_params['type']
        sshconfigures['Bits'] = ssh_key_gen_params['bits']
        sshconfigures['Passphrase'] = ssh_key_gen_params['passphrase']
        self.__db.account['SshConfigures'].update(sshconfigures, ['Id'])
        
        sshkeys = self.__db.account['SshConfigures'].find_one(AccountId=account['Id'])
        sshkeys['IdOnGitHub'] = j_ssh['id']
        sshkeys['Title'] = j_ssh['title']
        sshkeys['Key'] = j_ssh['key']
        sshkeys['PrivateKey'] = ssh_key_gen_params['private_key']
        sshkeys['PublicKey'] = ssh_key_gen_params['public_key']
        sshkeys['Verified'] = self.__j2s.BoolToInt(j_ssh['verified'])
        sshkeys['ReadOnly'] = self.__j2s.BoolToInt(j_ssh['read_only'])
        sshkeys['CreatedAt'] = j_ssh['created_at']
        self.__db.account['SshKeys'].update(sshkeys, ['Id'])

    def __LoadSshKeyFile(self, args, sshconf):
        ssh_key_gen_params = {
            'type': None,
            'bits': None,
            'passphrase': None,
            'path_file_key_private': None,
            'path_file_key_public': None,
            'private_key': None,
            'public_key': None,
        }
        path_file_key_private = sshconf.GetPrivateKeyFilePath(args.ssh_host)
        path_file_key_public = sshconf.GetPublicKeyFilePath(args.ssh_host)
        ssh_key_gen_params.update({'path_file_key_public': path_file_key_public})
        ssh_key_gen_params.update({'path_file_key_private': path_file_key_private})
        print(ssh_key_gen_params['path_file_key_private'])
        print(ssh_key_gen_params['path_file_key_public'])
        # キーファイルから内容を読み取る
        with open(ssh_key_gen_params['path_file_key_private']) as f:
            ssh_key_gen_params['private_key'] = f.read()
        with open(ssh_key_gen_params['path_file_key_public']) as f:
            # 公開鍵ファイルはスペース区切りで`{ssh-rsa} {公開鍵} {コメント}`の形式になっている。
            # GitHubではコメント値は保持しない。よって`{ssh-rsa} {公開鍵}`の部分だけ渡す
            pub_keys = f.read().split()
            ssh_key_gen_params['public_key'] = pub_keys[0] + ' ' + pub_keys[1]
        # 暗号化強度の情報を取得する
        ssh_key_gen_params = self.__GetSshKeyGenList(ssh_key_gen_params)
        print(ssh_key_gen_params)
        return ssh_key_gen_params
    
    """
    SSH鍵ファイルの暗号化強度を取得する。
    ssh-keygen -l -f {秘密鍵ファイルパス}
    {bits} {AA:BB:CC...}  {comment} ({type})
    {bits}=`2048`, comment=`メアド@mail.com`, {type}=`(RSA)`
    """
    def __GetSshKeyGenList(self, ssh_key_gen_params):
        # 暗号化強度の情報を取得する
        cmd = 'ssh-keygen -l -f "{0}"'.format(ssh_key_gen_params['path_file_key_public'])
        print(cmd)
        p = subprocess.Popen(cmd, shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        stdout_data, stderr_data = p.communicate()
        stdout_utf8 = stdout_data.decode('utf-8')
        print(stdout_utf8)
        elements = stdout_utf8.split()
        print(elements)
        ssh_key_gen_params['bits'] = elements[0]
        elements[3] = elements[3][1:] # '(' 削除
        elements[3] = elements[3][:-1] # ')' 削除
        ssh_key_gen_params['type'] = elements[3].lower()
        return ssh_key_gen_params

    def __SshConnectCheck(self, host, config_user, path_file_key_private):
        command = "ssh -T {config_user}@{host}".format(config_user=config_user, host=host)
        print(command)
        subprocess.call(command, shell=True, universal_newlines=True)
        # Hi {user}! You've successfully authenticated, but GitHub does not provide shell access.

