#!/usr/bin/env python
# Uses Woosh and zimpy to Index ZIM files for searching

import sys
import os
import re
import signal
import logging
import argparse

from whoosh import index
from whoosh.analysis import StemmingAnalyzer
from whoosh.fields import TEXT, NUMERIC, ID, Schema
from whoosh.qparser import QueryParser

from iiab.zimpy import ZimFile
from iiab.whoosh_search import index_directory_path

# Install progress bar package as it is really needed
# to help understand where the processing is
from progressbar import ProgressBar, Percentage, Bar

# For rendering HTML to text for indexing
from html2text import html2text

logger = logging.getLogger()

DEFAULT_MIME_TYPES = ["text/html", "text/plain"]
DEFAULT_MEMORY_LIMIT = 256

# Strip these tags that contain text between them that should not be indexed
STRIP_TEXT_TAGS = ["script"]

def remove_html(text):
    soup = BeautifulSoup(text)

    body_tag = soup.find("body")

    # No body in contents of article
    if body_tag == None:
        return u''

    # Remove script tags since they contain "text"
    for strip_tag_name in STRIP_TEXT_TAGS:
        tag_find = body_tag.findAll(STRIP_TEXT_TAGS)
        if tag_find != None:
            for script_tag in tag_find:
                script_tag.replaceWith("")

    return body_tag.findAll(text=True)

def article_info_as_unicode(articles):
    for article_info in articles:
        # Make any strings into unicode objects
        for k,v in article_info.items():
            if type(v) is str:
                article_info[k] = unicode(v)
        yield article_info

def content_as_text(zim_obj, article_info, index):
    "Return the contents of an article at a given index from the ZIM file as text"

    raw_content = zim_obj.get_article_by_index(index)[0]

    try:
        content = raw_content.decode("utf-8")
    except:
        content = raw_content.decode("latin1")
    
    # Strip out HTML so it is not indexed
    # It also converts to unicode in the process
    # Only do the stripping on HTML article types
    if "html" in zim_obj.mimeTypeList[article_info['mimetype']]:
        try:
            content = html2text(content)
        except ValueError:
            logger.error("Failed converting html to text from: %s at index: %d, skipping article" % (os.path.basename(zim_file), idx))
            content = None

    return content

def get_schema():
    # Create schema use by all indexes
    # Use an unbounded cache for StemmingAnalyzer to speed up indexing
    schema = Schema(title=TEXT(stored=True), 
                    url=ID(stored=True),
                    content=TEXT(stored=False, analyzer=StemmingAnalyzer(cachesize = -1)),
                    blobNumber=NUMERIC(stored=True),
                    namespace=ID(stored=True),
                    fullUrl=ID,
                    clusterNumber=NUMERIC,
                    mimetype=NUMERIC,
                    parameter=ID,
                    parameterLen=NUMERIC,
                    revision=NUMERIC,)
    return schema

def index_zim_file(zim_filename, output_dir=".", index_contents=True, mime_types=DEFAULT_MIME_TYPES, memory_limit=DEFAULT_MEMORY_LIMIT, processors=1, **kwargs):
    zim_obj = ZimFile(zim_filename)

    logger.info("Indexing: %s" % zim_filename)

    if not index_contents:
        logger.info("Not indexing article contents")        

    # Figure out which mime type indexes from this file we will use
    logger.debug("All mime type names: %s" % zim_obj.mimeTypeList)
    logger.info("Using mime types:")
    mime_type_indexes = []
    for mt_re in mime_types:
        for mt_idx, mt_name in enumerate(zim_obj.mimeTypeList):
            if re.search(mt_re, mt_name):
                mime_type_indexes.append(mt_idx)
                logger.info(mt_name)

    index_dir = index_directory_path(output_dir, zim_filename)
    if not os.path.exists(index_dir):
        logger.debug("Creating index directory: %s" % index_dir)
        os.mkdir(index_dir)

    # Don't overwrite an existing index
    if index.exists_in(index_dir):
        logger.debug("Loading existing index")
        ix = index.open_dir(index_dir)
    else:
        logger.debug("Creating new index")
        ix = index.create_in(index_dir, schema)

    writer = ix.writer(limitmb=memory_limit, proc=processors)

    # Set up a function to be called when a signal is thrown to commit
    # what was indexed so far in the case of kills
    def finish(*args):
        logger.info("Commiting index")
        zim_obj.close()
        writer.commit()
        sys.exit(1)

    signal.signal(signal.SIGTERM, finish)

    pbar = ProgressBar(widgets=[Percentage(), Bar()], maxval=zim_obj.header['articleCount']).start()

    try:
        for idx, article_info in enumerate(article_info_as_unicode(zim_obj.articles())):
            # Skip articles of undesired mime types
            if article_info['mimetype'] not in mime_type_indexes:
                continue

            if index_contents:
                content = content_as_text(zim_obj, article_info, idx)
            else:
                content = None

            writer.add_document(content=content, **article_info)

            pbar.update(idx+1)

        pbar.finish()
    except KeyboardInterrupt:
        logger.info("Indexing interrupted, will try and commit")

    finish() 

def main(argv):
    parser =  argparse.ArgumentParser(description="Indexes the contents of a ZIM file using Woosh")
    parser.add_argument("zim_files", nargs="+", 
                        help="ZIM files to index")
    parser.add_argument("-o", "--output-dir", dest="output_dir", action="store",
                        default="./zim-index",
                        help="The base directory where Woosh indexes are written. One sub directory per file.")
    parser.add_argument("-m", "--mime-types", dest="mime_types",
                        metavar="MIME_TYPE", nargs="*", 
                        default=DEFAULT_MIME_TYPES,
                        help="Mimetypes of articles to index")
    parser.add_argument("--no-contents", dest="index_contents", action="store_false",
                        default=True,
                        help="Turn of indexing of article contents")
    parser.add_argument("--memory-limit", dest="memory_limit", action="store",
                        default=DEFAULT_MEMORY_LIMIT, type=int,
                        help="Set maximum memory in Mb to consume by writer")
    parser.add_argument("--processors", dest="processors", action="store",
                        default=1, type=int,
                        help="Set the number of processors for use by the writer")
    parser.add_argument("-v", dest="verbose", action="store_true",
                        help="Turn on verbose logging")

    args = parser.parse_args()

    # Set up logging
    if args.verbose:
        logger.setLevel(logging.DEBUG)
    else:
        logger.setLevel(logging.INFO)
    console = logging.StreamHandler(stream=sys.stdout)
    logger.addHandler(console)

    # Create base directory for indexes
    if not os.path.exists(args.output_dir):
        logger.debug("Creating output dir: %s" % args.output_dir)
        os.mkdir(args.output_dir)

    logger.debug("Using schema: %s" % get_schema())

    for zim_file in args.zim_files:
        index_zim_file(zim_file, **args.__dict__)


if __name__ == "__main__":
    main(sys.argv)
