from glob import glob
import json
import logging
import os
import random
import re
from string import punctuation
import time
import xml.etree.ElementTree as ET

from gensim.models.doc2vec import LabeledSentence, Doc2Vec
from nltk import sent_tokenize, WordPunctTokenizer
import requests
from tika import parser as tikaparser

from hamlet.theses.models import Thesis

# TODO:
# It's 843G of docs (though this may include pdf and I only need txt).
# I only have 402 on my machine. So I need to plan on
# starting with a subset - which I should *anyway* - but I also need to think
# about what's resident in memory when.
# Consider scrapping the coroutine - do a first pass where you fetch all the
# things, cleaning the pdfs, and a second where you list directory contents.

# See https://medium.com/@klintcho/doc2vec-tutorial-using-gensim-ab3ac03d3a1

logger = logging.getLogger(__name__)

CUR_DIR = os.path.dirname(os.path.realpath(__file__))
with open(CUR_DIR + '/thesis_set_list.json', 'r') as f:
    THESIS_SET_LIST = json.loads(f.read())

DSPACE_OAI_IDENTIFIER = os.environ.get('DSPACE_OAI_IDENTIFIER')
DSPACE_OAI_URI = os.environ.get('DSPACE_OAI_URI')

# Where to put the files we train on.
FILES_DIR = 'files'
# First is training set; second is test set.
FILES_SUBDIRS = ['training', 'test']

METS_NAMESPACE = {'mets': 'http://www.loc.gov/METS/',
                  'mods': 'http://www.loc.gov/mods/v3',
                  'oai': 'http://www.openarchives.org/OAI/2.0/',
                  'oai_dc': 'http://www.openarchives.org/OAI/2.0/oai_dc/',
                  'dc': 'http://purl.org/dc/elements/1.1/',
                  'xsi': 'http://www.w3.org/2001/XMLSchema-instance'}


class LabeledLineSentence(object):
    def __init__(self):
        doc_list = glob(os.path.join('.', FILES_DIR, FILES_SUBDIRS[0], '*'))
        self.doc_list = [doc for doc in doc_list if os.path.isfile(doc)]

    def __iter__(self):
        for filename in self.doc_list:
            with open(filename, 'r') as f:
                doc = f.read()
            yield LabeledSentence(words=self._tokenize(doc), tags=[filename])

    def _tokenize(self, doc):
        all_tokens = []
        sentences = sent_tokenize(doc)

        tokenizer = WordPunctTokenizer()
        for sentence in sentences:
            words = tokenizer.tokenize(sentence.lower())
            words = [word for word in words if word not in punctuation]
            all_tokens.extend(words)
        return all_tokens


class MetadataWriter(object):
    def extract_contributors(self, metadata):
        # Includes advisor and department.
        contributors = metadata.findall('.//dc:contributor', METS_NAMESPACE)
        advisors = []
        departments = []

        for contributor in contributors:
            text = contributor.text
            if any(['Massachusetts Institute' in text,
                    'Dept' in text,
                    'Department' in text]):
                departments.append(text)
            else:
                advisors.append(text)
        return advisors, departments

    def extract_date(self, metadata):
        # There will be several (representing copyright, accessioning, etc.)
        # The copyright date will be a four-digit year. Find the earliest
        # year (there may be a substantial difference between copyright year
        # and archival processing years).
        date_format = r'^[0-9]{4}$'
        dates = metadata.findall('.//dc:date', METS_NAMESPACE)
        earliest = 20000
        for date in dates:
            if re.match(date_format, date.text):
                year = int(date.text)
                if year < earliest:
                    earliest = year
        return earliest

    def extract_identifier(self, metadata):
        identifiers = metadata.findall('.//dc:identifier', METS_NAMESPACE)
        id_str = None
        matcher = r'http[s]?://hdl.handle.net/1721.1/([0-9]*)'

        # There may be multiple identifiers; find the first that looks like a
        # handle.
        for identifier in identifiers:
            try:
                id_str = re.match(matcher, identifier.text).groups()[0]
                return int(id_str)
            except AttributeError:
                continue

    def extract_metadata(self, metadata_dc, metadata_mets):
        try:
            dc = ET.fromstring(metadata_dc)
            mets = ET.fromstring(metadata_mets)
        except ET.ParseError:
            return None

        authors = dc.findall('.//dc:creator', METS_NAMESPACE)
        advisors, departments = self.extract_contributors(dc)
        date = self.extract_date(dc)
        id = self.extract_identifier(dc)
        title = self.extract_title(mets)
        url = self.extract_url(mets)

        return {'authors': [author.text for author in authors],
                'advisors': advisors,
                'date': date,
                'departments': departments,
                'id': id,
                'title': title,
                'url': url}

    def extract_title(self, mets):
        title = mets.find('.//mods:title', METS_NAMESPACE)
        try:
            return title.text
        except:
            return ''

    def extract_url(self, mets):
        record = mets.find('.//mets:file[@MIMETYPE="application/pdf"]/',
                           METS_NAMESPACE)
        url = record.get('{http://www.w3.org/1999/xlink}href')
        if url:
            url = url.replace('http://', 'https://')

        return url

    def write(self, metadata_dc, metadata_mets):
        datadict = self.extract_metadata(metadata_dc, metadata_mets)
        if not datadict:
            return False
        print(datadict)
        try:
            Thesis.objects.get(identifier=datadict['id'])
        except Thesis.DoesNotExist:
            thesis = Thesis.objects.create(
                title=datadict['title'],
                url=datadict['url'],
                year=datadict['date'],
                identifier=datadict['id']
            )
            print('Created {}'.format(thesis.id))
            thesis.add_people(datadict['authors'])
            thesis.add_people(datadict['advisors'], author=False)
            """
            department = # how to handle multiple
            degree = # need to extract; not recorded in an obvious way
            """

        return True


class DocFetcher(object):
    DOCS_CACHE = {}
    WRITER = MetadataWriter()

    def extract_text(self, item):
        if 'textfile' in self.DOCS_CACHE[item['handle']].keys():
            with open(self.DOCS_CACHE[item['handle']]['textfile'], 'r') as f:
                return f.read()

        fn = self.DOCS_CACHE[item['handle']]['filename']
        parsed = tikaparser.from_file(fn)
        content = parsed['content']
        textfile = self.split_data(item)
        with open(textfile, 'w') as f:
            f.write(content)

        self.DOCS_CACHE[item['handle']]['textfile'] = textfile
        os.remove(fn)

        return content

    def get_network_files(self, args, start_date=None, end_date=None):
        print('Network files!!!!!!')
        items = self.get_record_list(DSPACE_OAI_URI, start_date, end_date)
        parsed_items = self.parse_record_list(items)
        total_items_processed = 0

        for item in parsed_items:
            if item['handle'] not in self.DOCS_CACHE.keys():
                self.DOCS_CACHE[item['handle']] = {}

            if not self.is_thesis(item):
                continue
            print('Processing {}'.format(item['handle']))

            total_items_processed += 1

            print('Processing item %s' % item['handle'])
            if 'textfile' not in self.DOCS_CACHE[item['handle']].keys():
                self.get_single_network_file(item, args)
                if not args['dryrun']:
                    self.extract_text(item)

    def get_pdf_url(self, metadata_mets):
        '''Gets and returns download URL for PDF from METS record.
        '''
        mets = ET.fromstring(metadata_mets)
        record = mets.find('.//mets:file[@MIMETYPE="application/pdf"]/',
                           METS_NAMESPACE)
        if not record:
            # There is at least one thesis which has been withdrawn; it has a
            # dspace handle but does not return a document.
            return

        url = record.get('{http://www.w3.org/1999/xlink}href')
        if url:
            url = url.replace('http://', 'https://')

        return url

    def get_record(self, dspace_oai_uri, dspace_oai_identifier, identifier,
                   metadata_format):
        '''Gets metadata record for a single item in OAI-PMH repository in
        specified metadata format.
        '''
        params = {'verb': 'GetRecord',
                  'identifier': dspace_oai_identifier + identifier,
                  'metadataPrefix': metadata_format}
        r = requests.get(dspace_oai_uri, params=params)
        return r.text

    def get_record_list(self, dspace_oai_uri, start_date=None,
                        end_date=None):
        '''Returns a list of record headers for items in OAI-PMH repository.
        Must pass in desired metadata format prefix. Can optionally pass
        bounding dates to limit harvest.
        '''
        params = {'verb': 'ListIdentifiers', 'metadataPrefix': 'mets'}

        if start_date:
            params['from'] = start_date
        if end_date:
            params['until'] = end_date

        r = requests.get(dspace_oai_uri, params=params)
        return r.text

    def get_single_network_file(self, item, args):
        metadata_mets = self.get_record(DSPACE_OAI_URI, DSPACE_OAI_IDENTIFIER,
                                        item['identifier'], 'mets')

        # The available formats are oai_dc; qdc; rdf; ore; and mets. None of
        # them match the Dublin Core displayed at dspace.mit.edu, but rdf seems
        # to have the content we want. To get a list of all verbs, issue a
        # get request to the OAI endpoint with
        # params={'verb': 'ListMetadataFormats'}.
        metadata_dc = self.get_record(DSPACE_OAI_URI, DSPACE_OAI_IDENTIFIER,
                                      item['identifier'], 'rdf')

        pdf_url = self.get_pdf_url(metadata_mets)
        if args['write_metadata']:
            outcome = self.write_metadata(metadata_dc, metadata_mets)
            if not outcome:
                return

        if args['dryrun']:
            return

        if not pdf_url:
            return

        with open(item['handle'], 'wb') as f:
            r = requests.get(pdf_url, stream=True)
            r.raise_for_status()
            for chunk in r.iter_content(1024):
                f.write(chunk)
            f.flush()
            self.DOCS_CACHE[item['handle']]['filename'] = f.name

    def is_thesis(self, item):
        '''Returns True if any set_spec in given sets is in the
        thesis_set_spec_list, otherwise returns false.
        '''
        try:
            return self.DOCS_CACHE[item['handle']]['is_thesis']
        except KeyError:
            print(item['sets'])
            ans = any([s in THESIS_SET_LIST.keys() for s in item['sets']])
            self.DOCS_CACHE[item['handle']]['is_thesis'] = ans
            return ans

    def parse_record_list(self, record_xml):
        xml = ET.fromstring(record_xml)
        records = xml.findall('.//oai:header', METS_NAMESPACE)
        for record in records:
            handle = record.find('oai:identifier', METS_NAMESPACE).text\
                .replace('oai:dspace.mit.edu:', '').replace('/', '-')
            identifier = handle.replace('1721.1-', '')
            setSpecs = record.findall('oai:setSpec', METS_NAMESPACE)
            sets = [s.text for s in setSpecs]
            yield {'handle': handle, 'identifier': identifier, 'sets': sets}

    def split_data(self, item):
        """
        Randomly assigns to training or test set. Weighted, such that 80%
        of objects end up in the training set.
        """
        set_dir = random.choices(FILES_SUBDIRS, weights=[8, 2])[0]
        return '{}/{}/{}/{}.txt'.format(CUR_DIR, FILES_DIR, set_dir,
            self.DOCS_CACHE[item['handle']]['filename'])

    def write_metadata(self, metadata_dc, metadata_mets):
        return self.WRITER.write(metadata_dc, metadata_mets)


class ModelTrainer(object):
    def execute(self, args):
        print('~~~~~~~ getting network files')
        fetcher = DocFetcher()
        fetcher.get_network_files(args)
        print('~~~~~~~ training model')
        if not args['dryrun']:
            self.train_model(args['filename'])

    def get_iterator(self):
        return LabeledLineSentence()

    def train_model(self, filename):
        doc_iterator = self.get_iterator()

        for window in range(3, 10):
            for step in range(1, 5):
                size = step * 50
                start_time = time.time()

                print('Training with parameters window={}, '
                      'size={}'.format(window, size))
                model = Doc2Vec(alpha=0.025,
                                # Alpha starts at `alpha` and decreases to
                                # `min_alpha`
                                min_alpha=0.025,
                                # Size of DBOW window (default=5).
                                window=window,
                                # Feature vector dimensionality (default=100).
                                size=size,
                                # Min word frequency for inclusion (default=5).
                                min_count=10)
                print("Building vocab for %s..." % filename)
                model.build_vocab(doc_iterator)

                model.train(doc_iterator,
                            total_examples=model.corpus_count,
                            epochs=model.iter)

                fn = '{}_w{}_s{}'.format(filename, window, size)
                model.save('nets/{}.model'.format(fn))
                print('Finished training, took {}'.format(
                    time.time() - start_time))
