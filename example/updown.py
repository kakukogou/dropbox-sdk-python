"""Upload the contents of your Downloads folder to Dropbox.

This is an example app for API v2.
"""

from __future__ import print_function

import argparse
import contextlib
import datetime
import os
import six
import sys
import time
import unicodedata
import io
import random
import string

if sys.version.startswith('2'):
    input = raw_input  # noqa: E501,F821; pylint: disable=redefined-builtin,undefined-variable,useless-suppression

import dropbox

# OAuth2 access token.  TODO: login etc.
TOKEN = ''

parser = argparse.ArgumentParser(description='Sync ~/Downloads to Dropbox')
parser.add_argument('folder', nargs='?', default='Downloads',
                    help='Folder name in your Dropbox')
parser.add_argument('rootdir', nargs='?', default='~/Downloads',
                    help='Local directory to upload')
parser.add_argument('--token', default=TOKEN,
                    help='Access token '
                    '(see https://www.dropbox.com/developers/apps)')
parser.add_argument('--yes', '-y', action='store_true',
                    help='Answer yes to all questions')
parser.add_argument('--no', '-n', action='store_true',
                    help='Answer no to all questions')
parser.add_argument('--default', '-d', action='store_true',
                    help='Take default answer on all questions')
# Add the new argument for team member ID
parser.add_argument('--team-member-id', default=None,
                    help='Team member ID for Dropbox Business account (required for team access tokens)')
parser.add_argument('--depth', type=int, default=2,
                    help='Depth of the folder hierarchy')
parser.add_argument('--subfolders', type=int, default=3,
                    help='Number of subfolders at each level')
parser.add_argument('--files', type=int, default=3,
                    help='Number of files in each folder')
parser.add_argument('--file-size', type=int, default=1024,
                    help='Size of each generated file in bytes')

def main():
    """Main program.

    Parse command line, then iterate over files and directories under
    rootdir and upload all files.  Skips some temporary files and
    directories, and avoids duplicate uploads by comparing size and
    mtime with the server.
    """
    args = parser.parse_args()
    
    # Check for required arguments
    if sum([bool(b) for b in (args.yes, args.no, args.default)]) > 1:
        print('At most one of --yes, --no, --default is allowed')
        sys.exit(2)
    if not args.token:
        print('--token is mandatory')
        sys.exit(2)
    if args.team_member_id is None:
        print('--team-member-id is required for team access tokens')
        sys.exit(2)
    
    # Initialize Dropbox with team member ID
    dbx = dropbox.Dropbox(args.token, headers={"Dropbox-API-Select-User": args.team_member_id})
    
    # Upload local folder onto Dropbox
    # upload_from_local_folder(dbx, args)

    # Generate folder structure and upload it
    generate_and_upload_structure(dbx, args.folder, args.depth, args.subfolders, args.files, args.file_size)

    dbx.close()

def upload_from_local_folder(dbx, args):

    folder = args.folder
    rootdir = os.path.expanduser(args.rootdir)
    print('Dropbox folder name:', folder)
    print('Local directory:', rootdir)
    if not os.path.exists(rootdir):
        print(rootdir, 'does not exist on your filesystem')
        sys.exit(1)
    elif not os.path.isdir(rootdir):
        print(rootdir, 'is not a folder on your filesystem')
        sys.exit(1)

    for dn, dirs, files in os.walk(rootdir):
        subfolder = dn[len(rootdir):].strip(os.path.sep)
        listing = list_folder(dbx, folder, subfolder)
        print('Descending into', subfolder, '...')

        # First do all the files.
        for name in files:
            fullname = os.path.join(dn, name)
            if not isinstance(name, six.text_type):
                name = name.decode('utf-8')
            nname = unicodedata.normalize('NFC', name)
            if name.startswith('.'):
                print('Skipping dot file:', name)
            elif name.startswith('@') or name.endswith('~'):
                print('Skipping temporary file:', name)
            elif name.endswith('.pyc') or name.endswith('.pyo'):
                print('Skipping generated file:', name)
            elif nname in listing:
                md = listing[nname]
                mtime = os.path.getmtime(fullname)
                mtime_dt = datetime.datetime(*time.gmtime(mtime)[:6])
                size = os.path.getsize(fullname)
                if (isinstance(md, dropbox.files.FileMetadata) and
                        mtime_dt == md.client_modified and size == md.size):
                    print(name, 'is already synced [stats match]')
                else:
                    print(name, 'exists with different stats, downloading')
                    res = download(dbx, folder, subfolder, name)
                    with open(fullname) as f:
                        data = f.read()
                    if res == data:
                        print(name, 'is already synced [content match]')
                    else:
                        print(name, 'has changed since last sync')
                        if yesno('Refresh %s' % name, False, args):
                            upload(dbx, fullname, folder, subfolder, name,
                                   overwrite=True)
            elif yesno('Upload %s' % name, True, args):
                upload(dbx, fullname, folder, subfolder, name)

        # Then choose which subdirectories to traverse.
        keep = []
        for name in dirs:
            if name.startswith('.'):
                print('Skipping dot directory:', name)
            elif name.startswith('@') or name.endswith('~'):
                print('Skipping temporary directory:', name)
            elif name == '__pycache__':
                print('Skipping generated directory:', name)
            elif yesno('Descend into %s' % name, True, args):
                print('Keeping directory:', name)
                keep.append(name)
            else:
                print('OK, skipping directory:', name)
        dirs[:] = keep

def generate_and_upload_structure(dbx, root_folder, depth, subfolder_count, file_count, file_size):
    """Generate folder and file structure in memory and upload to Dropbox."""
    def random_string(length=10):
        return ''.join(random.choices(string.ascii_letters + string.digits, k=length))

    def upload_file(dbx, folder_path, file_name, file_size):
        """Upload a generated file to Dropbox."""
        content = io.BytesIO(random_string(file_size).encode('utf-8'))
        path = f'/{folder_path}/{file_name}'
        with stopwatch(f'upload {file_name} to {folder_path}'):
            try:
                dbx.files_upload(content.read(), path, mode=dropbox.files.WriteMode.overwrite)
                print(f"Uploaded file: {path}")
            except dropbox.exceptions.ApiError as err:
                print(f"Failed to upload {file_name} to {folder_path}: {err}")

    def create_folder(dbx, folder_path):
        """Create a folder in Dropbox."""
        path = f'/{folder_path}'
        try:
            dbx.files_create_folder_v2(path)
            print(f"Created folder: {path}")
        except dropbox.exceptions.ApiError as err:
            if err.error.is_path() and err.error.get_path().is_conflict():
                print(f"Folder already exists: {path}")
            else:
                print(f"Failed to create folder {path}: {err}")

    def generate_structure(dbx, current_path, current_depth):
        """Recursively generate folders and files, uploading them to Dropbox."""
        if current_depth > depth:
            return
        create_folder(dbx, current_path)
        
        # Create files
        for _ in range(file_count):
            file_name = f"{random_string(8)}.txt"
            upload_file(dbx, current_path, file_name, file_size)
        
        # Create subfolders and recursively generate their structure
        for _ in range(subfolder_count):
            subfolder_name = f"{random_string(8)}"
            generate_structure(dbx, f"{current_path}/{subfolder_name}", current_depth + 1)

    # Start generating and uploading from the root folder
    generate_structure(dbx, root_folder, 1)

def list_folder(dbx, folder, subfolder):
    """List a folder.

    Return a dict mapping unicode filenames to
    FileMetadata|FolderMetadata entries.
    """
    path = '/%s/%s' % (folder, subfolder.replace(os.path.sep, '/'))
    while '//' in path:
        path = path.replace('//', '/')
    path = path.rstrip('/')
    try:
        with stopwatch('list_folder'):
            res = dbx.files_list_folder(path)
    except dropbox.exceptions.ApiError as err:
        print('Folder listing failed for', path, '-- assumed empty:', err)
        return {}
    else:
        rv = {}
        for entry in res.entries:
            rv[entry.name] = entry
        return rv

def download(dbx, folder, subfolder, name):
    """Download a file.

    Return the bytes of the file, or None if it doesn't exist.
    """
    path = '/%s/%s/%s' % (folder, subfolder.replace(os.path.sep, '/'), name)
    while '//' in path:
        path = path.replace('//', '/')
    with stopwatch('download'):
        try:
            md, res = dbx.files_download(path)
        except dropbox.exceptions.HttpError as err:
            print('*** HTTP error', err)
            return None
    data = res.content
    print(len(data), 'bytes; md:', md)
    return data

def upload(dbx, fullname, folder, subfolder, name, overwrite=False):
    """Upload a file.

    Return the request response, or None in case of error.
    """
    path = '/%s/%s/%s' % (folder, subfolder.replace(os.path.sep, '/'), name)
    while '//' in path:
        path = path.replace('//', '/')
    mode = (dropbox.files.WriteMode.overwrite
            if overwrite
            else dropbox.files.WriteMode.add)
    mtime = os.path.getmtime(fullname)
    with open(fullname, 'rb') as f:
        data = f.read()
    with stopwatch('upload %d bytes' % len(data)):
        try:
            res = dbx.files_upload(
                data, path, mode,
                client_modified=datetime.datetime(*time.gmtime(mtime)[:6]),
                mute=True)
        except dropbox.exceptions.ApiError as err:
            print('*** API error', err)
            return None
    print('uploaded as', res.name.encode('utf8'))
    return res

def yesno(message, default, args):
    """Handy helper function to ask a yes/no question.

    Command line arguments --yes or --no force the answer;
    --default to force the default answer.

    Otherwise a blank line returns the default, and answering
    y/yes or n/no returns True or False.

    Retry on unrecognized answer.

    Special answers:
    - q or quit exits the program
    - p or pdb invokes the debugger
    """
    if args.default:
        print(message + '? [auto]', 'Y' if default else 'N')
        return default
    if args.yes:
        print(message + '? [auto] YES')
        return True
    if args.no:
        print(message + '? [auto] NO')
        return False
    if default:
        message += '? [Y/n] '
    else:
        message += '? [N/y] '
    while True:
        answer = input(message).strip().lower()
        if not answer:
            return default
        if answer in ('y', 'yes'):
            return True
        if answer in ('n', 'no'):
            return False
        if answer in ('q', 'quit'):
            print('Exit')
            raise SystemExit(0)
        if answer in ('p', 'pdb'):
            import pdb
            pdb.set_trace()
        print('Please answer YES or NO.')

@contextlib.contextmanager
def stopwatch(message):
    """Context manager to print how long a block of code took."""
    t0 = time.time()
    try:
        yield
    finally:
        t1 = time.time()
        print('Total elapsed time for %s: %.3f' % (message, t1 - t0))

if __name__ == '__main__':
    main()
