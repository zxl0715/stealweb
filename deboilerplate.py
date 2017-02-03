import logging
import argparse
import codecs
import csv
import subprocess
import os
import re

from lxml import etree, objectify

def parseargs():
    parser = argparse.ArgumentParser(
                description='Remove boilerplate from file'
               ,formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    parser.add_argument("html", help="HTML file")
    parser.add_argument("csv", nargs='?', help="CSV file provided XPath and goodness. Default is HTML file with .csv suffix")
    args = parser.parse_args()
    if not args.csv:
        args.csv = args.html + '.csv'
    return args

def isempty(s):
    if not s: return True
    if isinstance(s,basestring) and not s.strip(): return True
    return False

def prune(elem):
    "remove an etree element and its descendent from the node tree but retain the tail text"
    parent = elem.getparent()
    if parent is None:
        return False # cannot remove
    if elem.tail:
        previous = elem.getprevious()
        if previous is not None:
            previous.tail = ' '.join(filter(None, [previous.tail, elem.tail]))
        else:
            parent.text = ' '.join(filter(None, [parent.text, elem.tail]))
    parent.remove(elem)
    return True

def delete(elem):
    "remove an etree element but retain its descendents and text"
    parent = elem.getparent()
    if parent is None:
        return False # cannot remove
    # if it has inner text, append it to the previous node's tail or parent's text
    if elem.text:
        previous = elem.getprevious()
        if previous is not None:
            previous.tail = ' '.join(filter(None, [previous.tail, elem.text]))
        else:
            parent.text = ' '.join(filter(None, [parent.text, elem.text]))
        elem.text = None
    # if it has tail, append it to the last child's tail
    if len(elem) == 0:
        return prune(elem) # no children, equivalent to prune
    if elem.tail:
        i = len(elem) - 1
        elem[i].tail = ' '.join(filter(None, [elem[i].tail, elem.tail]))
        elem.tail = None
    # insert all children to its parent at the same position
    me = [i for i,x in enumerate(parent) if x==elem]
    assert(len(me)==1) # should found myself uniquely
    me = me[0]
    while len(elem):
        parent.insert(me, elem[-1])
    # this element should be empty now, remove it from tree
    assert(not elem.tail and not elem.text and len(elem)==0)
    parent.remove(elem)
    return True


prefixOfSomething = lambda prefix, strset: any(x.startswith(prefix) for x in strset)
somethingIsPrefix = lambda prefix, strset: any(prefix.startswith(x) for x in strset)

###################################
# main program
#
def clean_html(csvfilename, htmlfilename):
    # read CSV, find good elements
    with open(csvfilename, "rb") as csvfile:
        if csvfile.read(3) != codecs.BOM_UTF8:
            csvfile.seek(0) # skip BOM if exists
        csvin = csv.reader(csvfile)
        table = [row for row in csvin]
        header, table = table[0], table[1:]

    i = header.index('xpath')
    j = header.index('goodness')
    goodxpaths = [row[i] for row in table if int(row[j])]
    logging.debug('%d good element found out of %d' % (len(goodxpaths), len(table)))
    assert(all(x and isinstance(x,basestring) for x in goodxpaths))

    # read HTML build DOM tree
    parser = etree.HTMLParser(encoding='utf8', remove_blank_text=True, remove_comments=True, remove_pis=True)
    htmlstr = open(htmlfilename).read()
    dom = etree.fromstring(htmlstr, parser)
    domtree = etree.ElementTree(dom)
    objectify.deannotate(domtree, cleanup_namespaces=True)

    # scan DOM tree, remove bad stuff
    domelems = list(domtree.iter())
    domxpaths = [domtree.getpath(e) for e in domelems]
    for elem, this_xpath in zip(domelems, domxpaths):
        if elem.attrib:
            keepattrib = ['href'] if elem.tag == 'a' else ['src','title','alt'] if elem.tag == 'img' else []
            for k in elem.attrib:
                if  k not in keepattrib:
                    del elem.attrib[k]
        if not this_xpath:
            logging.debug('?? %s %s' % (elem, repr(this_xpath)))
            continue # no xpath found, probably deleted?
        elif elem.tag in ['script','meta']:
            # some element is removable for sure
            if not prune(elem):
                logging.error('Cannot find parent of %s' % this_xpath)
            else:
                logging.debug('Removed %s' % this_xpath)
        elif this_xpath in goodxpaths:
            logging.debug('Keep good element %s' % this_xpath)
            continue # this is not boilerplate, keep it
        elif not prefixOfSomething(this_xpath, goodxpaths) and not somethingIsPrefix(this_xpath, goodxpaths):
            # nothing is prefix of this and it is prefix of nothing -> unwanted tree branches
            parent = elem.getparent()
            if parent is not None:
                parent.remove(elem)
                logging.debug('Removed %s' % this_xpath)
            else:
                logging.error('Cannot find parent of %s' % this_xpath)
        elif not prefixOfSomething(this_xpath, goodxpaths) and somethingIsPrefix(this_xpath, goodxpaths):
            # something is prefix of this but it is prefix of nothing -> unwanted child but good tail
            if not prune(elem):
                logging.critical('Cannot removed %s for no parent found' % this_xpath)
            else:
                logging.debug('Removed %s but keep tail' % this_xpath)
        elif prefixOfSomething(this_xpath, goodxpaths) and not somethingIsPrefix(this_xpath, goodxpaths):
            # it is prefix of something but nothing is prefix of this -> remove all text but retain children
            if elem.tail: elem.tail = ''
            if elem.text: elem.text = ''
            for child in elem:
                if child.tail: child.tail = ''
            logging.debug('Removed text of %s but keep children' % this_xpath)
        elif prefixOfSomething(this_xpath, goodxpaths) and somethingIsPrefix(this_xpath, goodxpaths):
            # this is prefix of something and something is prefix of this but it
            # is not content -> keep tail but remove inner text
            if elem.text: elem.text = ''
            for child in elem:
                if child.tail: child.tail = ''
            logging.debug('Removed inner text of %s but keep children' % this_xpath)
        else:
            logging.error('Unhandled element %s' % this_xpath)

    # more clean up: remove some elements
    try_again = False
    allow_empty = ['br','tr','img']
    while True:
        for elem in domtree.iter():
            parent = elem.getparent()
            if parent is None: continue
            if len(elem) == 0 and isempty(elem.text) and elem.tag not in allow_empty:
                prune(elem)
                try_again = True
                break
            if len(elem) == 1 and elem.tag == 'div' and isempty(elem.text) and isempty(elem[0].tail):
                delete(elem)
                try_again = True
                break
            if elem.text and elem.tag not in ['pre','code']:
                elem.text = re.sub(r'\s+',' ',elem.text)
            if elem.tail and parent.tag not in ['pre','code']:
                elem.tail = re.sub(r'\s+',' ',elem.tail)
        if not try_again:
            break
        try_again = False

    # stringify cleaned HTML
    etree.strip_tags(domtree, 'span') # pandoc will keep span tag if not removed
    goodhtml = etree.tostring(domtree, encoding='utf-8', pretty_print=True, method="html")
    return goodhtml

def main(csv, html):
    goodhtml = clean_html(csv, html)
    open("debug.html","w").write(goodhtml)
    DEVNULL = open(os.devnull, 'w')
    p = subprocess.Popen(['pandoc','-f','html','-t','markdown_strict']
                        ,stdin=subprocess.PIPE
                        ,stdout=subprocess.PIPE
                        ,stderr=DEVNULL)
    outtext = p.communicate(goodhtml)[0]
    print outtext

if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO
                       ,format='%(asctime)-15s %(levelname)s:%(name)s:%(message)s')
    args = parseargs()
    main(args.csv, args.html)

# vim:set nowrap et ts=4 sw=4: