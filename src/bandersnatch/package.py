from . import utils
import glob
import hashlib
import logging
import os.path
import requests
import shutil
import urllib2

logger = logging.getLogger(__name__)


class Package(object):

    def __init__(self, name, mirror):
        self.name = name
        self.mirror = mirror

    @property
    def package_directories(self):
        return glob.glob(os.path.join(
            self.mirror.webdir, 'packages/*/{}/{}'.format(self.name[0], self.name)))

    @property
    def package_files(self):
        return glob.glob(os.path.join(
            self.mirror.webdir, 'packages/*/{}/{}/*'.format(self.name[0], self.name)))

    @property
    def simple_directory(self):
        return os.path.join(self.mirror.webdir, 'simple', self.name)

    @property
    def serversig_file(self):
        return os.path.join(self.mirror.webdir, 'serversig', self.name)

    @property
    def directories(self):
        return self.package_directories + [self.simple_directory]

    def sync(self):
        try:
            logger.info('Syncing package: {}'.format(self.name))
            self.releases = self.mirror.master.package_releases(self.name)
            if not self.releases:
                self.delete()
                return
            self.sync_release_files()
            self.sync_simple_page()
        except Exception:
            logger.exception('Error syncing package: {}'.format(self.name))
            self.mirror.errors = True
        else:
            self.mirror.record_finished_package(self.name)

    def sync_release_files(self):
        release_files = []

        for release in self.releases:
            release_files.extend(self.mirror.master.release_urls(
                self.name, release))

        self.purge_files(release_files)

        for release_file in release_files:
            self.download_file(release_file['url'], release_file['md5_digest'])

    def sync_simple_page(self):
        logger.info('Syncing index page: {}'.format(self.name))
        # The trailing slash is important. There are packages that have a
        # trailing ? that will get eaten by the webserver even if we quote it
        # properly. Yay.
        r = requests.get(self.mirror.master.url+'/simple/'+urllib2.quote(self.name)+'/')
        r.raise_for_status()

        if not os.path.exists(self.simple_directory):
            os.makedirs(self.simple_directory)

        simple_page = os.path.join(self.simple_directory, 'index.html')
        with open(simple_page, 'wb') as f:
            f.write(r.content)

        r = requests.get(self.mirror.master.url+'/serversig/'+urllib2.quote(self.name)+'/')
        r.raise_for_status()
        with open(self.serversig_file, 'wb') as f:
            f.write(r.content)

    def _file_url_to_local_path(self, url):
        path = url.replace(self.mirror.master.url, '')
        if not path.startswith('/packages'):
            raise RuntimeError('Got invalid download URL: {}'.format(url))
        path = path[1:]
        return os.path.join(self.mirror.webdir, path)

    def purge_files(self, release_files):
        master_files = [self._file_url_to_local_path(f['url'])
                        for f in release_files]
        existing_files = list(self.package_files)

    def download_file(self, url, md5sum):
        path = self._file_url_to_local_path(url)
        tmppath = os.path.join(os.path.dirname(path),
                               '.downloading.'+os.path.basename(path))

        # Avoid downloading again if we have the file and it matches the hash.
        if os.path.exists(path):
            existing_hash = utils.hash(path)
            if existing_hash == md5sum:
                return
            else:
                logger.warning('Checksum error with local file {}: expected {} got {}'.format(
                    path, md5sum, existing_hash))
                os.unlink(path)

        logger.info('Downloading: {}'.format(url))

        dirname = os.path.dirname(path)
        if not os.path.exists(dirname):
            os.makedirs(dirname)

        r = requests.get(url, stream=True)
        r.raise_for_status()
        checksum = hashlib.md5()
        with open(tmppath, "wb") as f:
            for chunk in r.iter_content(chunk_size=64*1024):
                checksum.update(chunk)
                f.write(chunk)

        if checksum.hexdigest() != md5sum:
            os.unlink(tmppath)
            raise ValueError('{} has hash {} instead of {}'.format(
                url, existing_hash, md5sum))
        os.rename(tmppath, path)

    def delete(self):
        logger.info('Deleting package: {}'.format(self.name))
        for directory in self.directories:
            if not os.path.exists(directory):
                continue
            shutil.rmtree(directory)
        if os.path.exists(self.serversig_file):
            os.unlink(self.serversig_file)
