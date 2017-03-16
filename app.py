from collections import OrderedDict

from flask import Flask, render_template, request, send_from_directory, redirect, flash
from urllib.parse import urlparse
from git import Repo
from markdown import markdown
import json
import os
import re
import sh
from datetime import datetime
from slugify import slugify

app = Flask(__name__)
app.debug = True
app.config['DEFAULT_PROTOTYPE'] = '/home/gitbook/gitbook-prototypes/gitbook-prototype.git'
app.config['REPO_HOME'] = '/home/gitbook/book-repos'
app.config['BOOK_HOME'] = '/home/gitbook/gitbook-books'
app.config['VERSION'] = '1.2'
app.config['GITBOOK_WORKERS'] = 2
app.config['GITBOOK_BUILD_TIMEOUT'] = 5
app.config['IS_BUILDING'] = {}
app.secret_key = '12345'

class BuildFailed(Exception):
    def __init__(self, edition, exc, start_time):
        self.exc = exc
        self.edition = edition
        self.start_time = start_time

    def __str__(self):
        return "Build of {} : {} edition at {} failed because of {}:\n\t{}".format(
            self.edition.book.name,
            self.edition.name,
            self.start_time.isoformat(),
            self.exc.__class__.__name__,
            str(self.exc)
        )

#
# Book class
#


class Book(object):
    def __init__(self, name):
        self.name = name.rsplit('.git')[0]
        self.repo_path = os.path.join(app.config['REPO_HOME'], name)
        if not os.path.exists(self.repo_path):
            self.repo_path += '.git'
        self.book_path = os.path.join(app.config['BOOK_HOME'], name.rsplit('.git')[0])
        self.repo = Repo(self.repo_path)
        try:
            self.book_repo = Repo(self.book_path)
        except:
            self.book_repo = self.repo.clone(self.book_path)

        self._editions = []

    def build(self):
        for edition in self.editions:
            edition.build()

    @classmethod
    def create(cls, name, prototype=app.config['DEFAULT_PROTOTYPE']):
        slugged_name = slugify(name)
        print(slugged_name)
        new_repo_path = os.path.join(app.config['REPO_HOME'], slugged_name)
        new_book_path = os.path.join(app.config['BOOK_HOME'], slugged_name)
        new_repo = Repo(prototype).clone(new_repo_path, bare=True)
        new_book_repo = new_repo.clone(new_book_path)


    def new_edition(self, name, derived_from='book.json'):
        slugged_name = slugify(name)
        output_file = os.path.join(self.book_path, 'book.' + slugged_name + '.json')

        self.book_repo.remotes.origin.pull()
        with open(output_file, 'w') as ofile:
            with open(os.path.join(self.book_path, derived_from)) as ifile:
                config = json.load(ifile)
                json.dump(config, ofile)

        self.book_repo.index.add([output_file])
        self.book_repo.index.commit('added new edition')
        return Edition(self, slugged_name, config)

    @property
    def is_building(self):
        return app.config['IS_BUILDING'].get(self.name, False)

    @property
    def editions(self):
        if len(self._editions) == 0:
            self._editions = []

            for entry in os.scandir(self.book_path):
                is_book_json = re.match(r'book\.([A-Za-z0-9\-\_]+).json', entry.name)
                if is_book_json:
                    edition_name = is_book_json.group(1)
                    with open(entry.path) as config:
                        config = json.load(config)
                        self._editions.append(Edition(self, edition_name, config))
                elif entry.name == 'book.json':
                    edition_name = "master"
                    with open(entry.path) as config:
                        config = json.load(config)
                        self._editions.append(Edition(self, edition_name, config))

        return self._editions

    @property
    def last_updated(self):
        last_commit = next(self.repo.iter_commits())
        return last_commit.committed_datetime


class Edition(object):
    def __init__(self, book, name, config):
        self.book = book
        self.name = name
        self.has_presentation = 'remarkjs' in config.get('plugins', [])

    def build(self):
        """Not thread safe AT ALL."""
        start = datetime.now()
        cwd = os.getcwd()
        os.chdir(self.book.book_path)
        logfile = os.path.join('logs', 'build-{}-{}.out'.format(self.name, start.isoformat()))
        errfile = os.path.join('logs', 'build-{}-{}.err'.format(self.name, start.isoformat()))

        if not os.path.exists(self.book.book_path):
            self.book.book_repo.clone(self.book.book_path)
        else:
            self.book.book_repo.remotes.origin.pull()

        sh.mkdir('-p', 'public')
        sh.mkdir('-p', 'logs')

        with open(logfile, 'w') as out:
            with open(errfile, 'w') as err:
                try:
                    sh2 = sh(_out=out, _err=err)

                    sh2.mkdir('-p', 'public')
                    sh2.mkdir('-p', 'logs')

                    print("removing old files")
                    sh2.rm('-rf', os.path.join('public', self.name))
                    sh2.cp('book.{}.json'.format(self.name), 'book.json')

                    print("Refreshing gitbook installation")
                    sh2.gitbook('install')

                    print("Building website")
                    sh2.gitbook('build', '.', self.name)

                    print("Building PDF")
                    sh2.gitbook('pdf', '.')

                    print("Building MOBI")
                    sh2.gitbook('mobi', '.')

                    print("Building EPUB")
                    sh2.gitbook('epub', '.')

                    print("Moving data files")
                    sh2.mv(self.name, 'public')
                    sh2.mv('book.pdf', 'public/{}.pdf'.format(self.name))
                    sh2.mv('book.mobi', 'public/{}.mobi'.format(self.name))
                    sh2.mv('book.epub', 'public/{}.epub'.format(self.name))
                    sh2.git.checkout('book.json')

                    print("Building zip file")
                    os.chdir(os.path.join(self.book.book_path, 'public'))
                    sh2.zip('-r', self.name + '.zip', self.name)

                except Exception as e:
                    err.write('\n\n')
                    err.write('-'*80)
                    err.write(str(e))
                    raise BuildFailed(self, e, start)
                finally:
                    os.chdir(cwd)

    @property
    def is_building(self):
        return os.path.exists('/tmp/{}'.format(self.name))

    @property
    def read_url(self):
        return '/book/{}/{}/index.html'.format(self.book.name, self.name)

    @property
    def pdf_download_url(self):
        return '/download/{}/{}.pdf'.format(self.book.name, self.name)

    @property
    def mobi_download_url(self):
        return '/download/{}/{}.mobi'.format(self.book.name, self.name)

    @property
    def epub_download_url(self):
        return '/download/{}/{}.epub'.format(self.book.name, self.name)

    @property
    def html_download_url(self):
        return '/download/{}/{}.zip'.format(self.book.name, self.name)

    @property
    def presentation_url(self):
        return '/book/{}/{}/presentation.html'.format(self.book.name, self.name)

    @property
    def log_url(self):
        return '/logs/{}'.format(self.book.name)

    @property
    def logfiles(self):
        mylogs = sorted([p for p in os.scandir(os.path.join(self.book.book_path, 'logs')) if self.name in p.name and not p.name.startswith('.')], key=lambda x: x.name, reverse=True)
        return zip(
            (x for x in mylogs if x.name.endswith('.out')),
            (y for y in mylogs if y.name.endswith('.err'))
        )


#
# Jinja2 Filters
#

@app.template_filter('mtime')
def mtime(direntry):
    return datetime.fromtimestamp(direntry.stat().st_mtime).isoformat()


#
# Routes
#


@app.route("/")
def index():
    hostname = urlparse(request.url).hostname

    entries = os.scandir(app.config['REPO_HOME'])
    books = (Book(repo.name) for repo in entries if repo.is_dir() and repo.name[0] != '.')
    return render_template(
        'book-list.template.html',
        books=books,
        hostname=hostname,
        version=app.config['VERSION']
    )


@app.route("/create", methods=['POST'])
def create_book():
    slugged_name = slugify(request.form['name'])
    if not os.path.exists(os.path.join(app.config['REPO_HOME'], slugged_name)):
        Book.create(slugged_name)
        flash('Successfully created {}'.format(request.form['name']))
    else:
        flash('{} already exists, cowardly refusing to overwrite the existing one.'.format(request.form['name']))

    return redirect('/')


@app.route('/delete/<book>', methods=['POST'])
def delete_book(book):
    slugged_name = slugify(book)
    if not os.path.exists(os.path.join(app.config['REPO_HOME'], slugged_name)):
        Book(request.form['name']).create(slugged_name)
        flash('Successfully created {}'.format(request.form['name']))
    else:
        flash('{} already exists, cowardly refusing to overwrite the existing one.'.format(request.form['name']))

    return redirect('/')


@app.route("/readme/<book>")
def readme(book):
    b = Book(book)
    with open(os.path.join(b.book_path, 'README.md')) as readme:
        return render_template('show-readme.template.html', readme_body=markdown(readme.read().split('???')[0]))


@app.route("/book/<book>/<edition>/<path:filename>")
def browse_book(book, edition, filename):
    b = Book(book)
    return send_from_directory(os.path.join(b.book_path, 'public', edition), filename)


@app.route("/book/<book>/<edition>/")
def book_index(book, edition):
    b = Book(book)
    return send_from_directory(os.path.join(b.book_path, 'public', edition), 'index.html')


@app.route("/download/<book>/<filename>")
def download_book_file(book, filename):
    b = Book(book)
    return send_from_directory(os.path.join(b.book_path, 'public'), filename)


@app.route("/logs/<book>")
def list_logs(book):
    b = Book(book)
    logs = OrderedDict()
    for edition in sorted(b.editions, key=lambda e: e.name):
        logs[edition.name] = edition.logfiles
    return render_template('list-logs.template.html', logs=logs, book=book,
            version=app.config['VERSION'])


@app.route("/logs/<book>/<filename>")
def download_log_file(book, filename):
    b = Book(book)
    return send_from_directory(os.path.join(b.book_path, 'logs'), filename)



if __name__ == "__main__":
    app.run(host='0.0.0.0', threaded=True)
