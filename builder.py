import os
import time
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler, LoggingEventHandler
from app import app, Book, BuildFailed
from multiprocessing import Process
import fasteners


class BookBuildingEventHandler(FileSystemEventHandler):
    def __init__(self):
        super(BookBuildingEventHandler, self).__init__()

        self.book_name_index = len(app.config['REPO_HOME'].split(os.path.sep))  # name of the book should be the first dir under the repo home
        self.last_modification_time = {}

    def on_any_event(self, event):
        print(event)
        book_name = event.src_path.split(os.path.sep)[self.book_name_index].rsplit('.git', 1)[0]
        app.config['IS_BUILDING'][book_name] = True

        if '/refs/heads/' in event.src_path:  # check to make sure a new commit is added otherwise there's no point to building.
            self.last_modification_time[book_name] = time.time()

    def process_modified_books(self):
        now = time.time()

        def _build(name):
            with fasteners.InterProcessLock('/tmp/{}'.format(name)):
                try:
                    Book(name).build()
                    print("Done!")
                except BuildFailed as e:
                    print(e)
                del app.config['IS_BUILDING'][name]

        for book, t in [x for x in self.last_modification_time.items()]:
            if now - t > app.config['GITBOOK_BUILD_TIMEOUT']:  # if it's been more than 5 seconds
                print("Building {}".format(book))

                p = Process(target=_build, args=(book,))
                p.start()
                del self.last_modification_time[book]

bbeh = BookBuildingEventHandler()
observer = Observer()
observer.schedule(bbeh, app.config['REPO_HOME'], recursive=True)
observer.start()

try:
    while True:
        time.sleep(1)
        bbeh.process_modified_books()

except KeyboardInterrupt:
    observer.stop()


observer.join()
