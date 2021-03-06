# Import built-in modules
import os, re
import copy
import time
import shelve

# Import external modules
from BeautifulSoup import BeautifulSoup as BS
from sqlalchemy.orm.exc import MultipleResultsFound
import lxml.html

# Set up HTML parser
import HTMLParser
parser = HTMLParser.HTMLParser()

# Project imports
import neurotrends as nt
from trendpath import *
from trenddb import *
from util import *

# Import sub-project modules
from download.pubsearch import *
from pattern import tags

# Import fmri-report
import sys
sys.path.append('/Users/jmcarp/Dropbox/projects/fmri-report/scripts')
import reportproc as rp

repchars = [
    ('- ', '-'),
    (u'\u2013', '-'),
    ('[\'\"\xa2\xa9\xae\x03]', ''),
    ('[\s\xa0]+', ' '),
]

repxcept = {
    'dept' : [
        ('[\'\"\xa2\xa9\xa3\x03]', ''),
        repchars[-1]
    ],
    'field' : [
        ('[\'\"\xa2\xa9\xa3\x03]', ''),
        repchars[-1]
    ],
}

#############
# Functions #
#############

def batchclearauths():
    
    nt.session.query(Author).delete()
    nt.session.execute('DELETE from articles_authors')

    nt.session.commit()

def batchclearattrib(usereport=False):
    """
    Clear all Attributes, Fields, and Snippets from database
    Arguments:
        usereport (bool): Just clear from articles in fmri-report
    """
    
    if not usereport:
     
        # Clear association tables
        nt.session.execute('DELETE FROM attribs_fields')
        nt.session.execute('DELETE FROM articles_attribs')

        # Clear tables
        nt.session.execute('DELETE FROM fields')
        nt.session.execute('DELETE FROM attribs')
        nt.session.execute('DELETE FROM snippets')
    
    else:
        
        # Clear fields from articles one by one
        arts = getreparts()
        for art in arts:
            clearattrib(toart(art), commit=False)

    # Save changes
    nt.session.commit()

def clearattrib(art, commit=True):
    """
    Clear Attribs from an article
    Arguments:
        art (Article): Article object
        commit (bool): Save changes to database?
    """
    
    art.attribs = []

    if commit:
        nt.session.commit()

def cleantxt(txt, ptns):
    
    ctxt = txt

    for ptn in ptns:
        ctxt = re.sub(ptn[0], ptn[1], ctxt)

    return ctxt

def getreparts():
    """
    Get articles from fmri-report
    """

    report = rp.ezread()
    arts = [art['pmid'] for art in report 
        if nt.session.query(Article).filter(Article.pmid == art['pmid']).count()]
    return arts

def batchartparse(usereport=False, groups=[]):
    """
    Extract meta-data from all articles
    Arguments:
        usereport (bool): Only process articles from fmri-report
        groups (list): Tag groups to process; if empty, process all groups
    """
    
    # Get articles
    if usereport:
        arts = getreparts()
    else:
        arts = nt.session.query(Article).all()

    # Extract tags
    for artidx in range(len(arts)):
        
        print 'Working on article %d of %d...' % (artidx + 1, len(arts))
        commit = artidx % 100 == 0
        artparse(arts[artidx], commit, groups=groups)

    # Save changes
    nt.session.commit()

def load_doc(art, doc_type):
    
    if doc_type == 'html':
        return loadhtml(art)
    elif doc_type == 'pdf':
        return loadpdf(art)
    elif doc_type == 'pmc':
        return loadpmc(art)
    raise Exception('Document type %s not implemented' % (doc_type))

def art_verify(art, doc_types):
    
    # Cast article to Article
    art = toart(art)

    # Get article info
    info = artinfo({'xml' : art.xml})

    # Quit if no abstract
    if info['abstxt'] is None:
        return {}

    # Tokenize abstract
    abs_txt = info['abstxt']
    abs_words = re.split('\s+', abstxt)
    abs_words = [word.lower() for word in abs_words]

    # Ignore punctuation
    for char in ['.', ',', ';', ':']:
        abs_words = [word.strip(char) for word in abs_words]
    
    # Initialize document proportions
    doc_prop = {}

    # Loop over document types
    for doc_type in doc_types:
        
        # Load document text
        doc_text = load_doc(art, doc_type)
        doc_text = to_unicode(doc_text)
        doc_text = doc_text.lower()
        
        # Get document proportions
        if doc_text:
            doc_words = [word for word in abs_words if doc_text.find(word) > -1]
            doc_prop[doc_type] = float(len(doc_words)) / len(abs_words)

    # Return document proportions
    return doc_prop

def artverify(art, html='', pdf=''):
    """
    Check whether HTML and PDF documents match abstract text
    Arguments:
        html (str): HTML text (optional)
        pdf (str): PDF text (optional)
    """

    # Cast article to Article
    art = toart(art)

    # Get article info
    info = artinfo({'xml' : art.xml})

    # Quit if no abstract
    if info['abstxt'] is None:
        return None, None

    # Tokenize abstract
    abstxt = info['abstxt']
    abswords = re.split('\s+', abstxt)
    abswords = [word.lower() for word in abswords]

    # Ignore punctuation
    for char in ['.', ',', ';', ':']:
        abswords = [word.strip(char) for word in abswords]

    # Load HTML
    if not html:
        html = loadhtml(art, overwrite=True)
    
    # Load PDF
    if not pdf:
        pdf = loadpdf(art)
        pdf = to_unicode(pdf)

    # To lower-case
    html = html.lower()
    pdf = pdf.lower()

    # Check HTML
    if html:
        htmlwords = [word for word in abswords if html.find(word) > -1]
        htmlprop = float(len(htmlwords)) / len(abswords)
    else:
        htmlprop = None

    # Check PDF
    if pdf:
        pdfwords = [word for word in abswords if pdf.find(word) > -1]
        pdfprop = float(len(pdfwords)) / len(abswords)
    else:
        pdfprop = None

    # Return
    return htmlprop, pdfprop

def loadpmc(art, method='soup'):
        
    art = toart(art)

    pmcfile = file_path(art.pmid, 'html', file_dirs)
    
    html = ''

    if art.pmcfile and os.path.exists(pmcfile):

        html = ''.join(open(pmcfile, 'r').readlines())
        try:
            html = parsehtml(html, method=method)
        except:
            return ''

        html = to_unicode(html)
        html = parser.unescape(html)

    return html

def loadhtml(art, overwrite=False, method='lxml', raw=False, verbose=False):
    """
    Load HTML text for an article
    Arguments:
        art (str/Article): PubMed ID or Article object
        overwrite (bool): Overwriting existing HTML file?
        method (str): Parsing method (lxml or soup)
        raw (bool): Use unparsed HTML?
        verbose (bool): Print status?
    """
    
    artobj = toart(art)
    htmltxt = ''

    htmlfile = file_path(art.pmid, 'html', file_dirs)
    chtmlfile = file_path(art.pmid, 'chtml', file_dirs)

    if overwrite and os.path.exists(chtmlfile):
        os.remove(chtmlfile)

    if artobj.htmlfile:

        if os.path.exists(chtmlfile) \
                and not overwrite \
                and not raw:

            # Load clean HTML
            chtml = shelve.open(chtmlfile)
            htmltxt = chtml['txt']

            # Done
            if verbose:
                print 'Finished loading HTML...'

        elif os.path.exists(htmlfile):
            
            # Convert to plain text
            html = ''.join(open(htmlfile, 'r').readlines())
            
            # Pad TDs
            html = re.sub(
                '<td(.*?)>(.*?)</td>', 
                '<td\\1> \\2 </td>', 
                html, 
                flags=re.I
            )
            
            # 
            if raw:
                return html
            
            # 
            try:
                htmltxt = parsehtml(html, method=method)
            except:
                return ''

            htmltxt = to_unicode(htmltxt)
            htmltxt = parser.unescape(htmltxt)

            # Save clean HTML
            chtml = shelve.open(chtmlfile)
            chtml['txt'] = htmltxt
            chtml.close()

            # Done
            if verbose:
                print 'Finished reading HTML...'

    return htmltxt

def loadpdf(art, verbose=False):
    """
    Load PDF text for an article
    Arguments:
        art (str/Article): PubMed ID or Article object
        verbose (bool): Print status?
    """

    artobj = toart(art)
    pdftxt = ''

    if artobj.pdftxtfile:

        pdftxtfile = file_path(artobj.pmid, 'pdftxt', file_dirs)

        if os.path.exists(pdftxtfile):
            
            s = shelve.open(pdftxtfile)
            pdftxt = s['pdfinfo']['txt']
            
            # Done
            if verbose:
                print 'Finished reading PDF...'

    return pdftxt

def artparse(art, commit=True, overwrite=False, verify=True, 
        groups=[], addsnips=False, verbose=False):
    """
    Extract meta-data from article and write to database
    Arguments:
        art (str/Article): PubMed ID or Article object
        commit (bool): Commit changes to database?
        overwrite (bool): Overwrite existing data?
        verify (bool): Verify abstract text?
        groups (list): Tag groups to process; if [], use all groups
        addsnips (bool): Save snippets to database?
        verbose (bool): Print status?
    """
    
    # Find article
    artobj = toart(art)
    
    # Initialize docs
    docs = []

    # Read HTML file
    htmltxt = loadhtml(artobj, overwrite=overwrite, verbose=verbose)

    # Read PDF text file
    pdftxt = loadpdf(artobj, verbose=verbose)
    
    # Verify documents
    if verify:
        htmlprop, pdfprop = artverify(artobj, htmltxt, pdftxt)
        artobj.htmlval = htmlprop
        artobj.pdfval = pdfprop
        verhtml = htmlprop is None or htmlprop >= 0.85
        verpdf = pdfprop is None or pdfprop >= 0.85
    else:
        verhtml = True
        verpdf = True
    
    # Add HTML document
    if htmltxt and verhtml:
        docs.append(htmltxt)

    # Add PDF document
    if pdftxt and verpdf:
        docs.append(pdftxt)
    
    # Quit if no docs
    if not docs:
        return

    # Process docs
    if groups:
        procsrc = dict([(group, tags[group]) for group in groups])
    else:
        procsrc = tags

    # Extract tags from documents
    taggroups = procdocs(docs, procsrc)

    # Add tags to database
    for groupname in taggroups:
        
        taggroup = taggroups[groupname]

        if not taggroup:
            continue

        for tag in taggroup['tags']:

            attobj = []

            # Build Attrib query
            attq = nt.session.query(Attrib)
            conq = []
            foundfield = True
            fields = {}
            for field in tag:
                fieldname = groupname + field
                fieldvalue = tag[field]
                try:
                    fieldobj = nt.session.query(Field).\
                        filter(
                            and_(
                                Field.name == fieldname, 
                                Field.value == fieldvalue
                            )
                        ).one()
                except MultipleResultsFound, e:
                    print e
                    raise
                except:
                    fieldobj = Field(name=fieldname, value=fieldvalue)
                    nt.session.add(fieldobj)
                    foundfield = False
                fields[fieldname] = fieldobj
                if foundfield:
                    conq.append(Attrib.fields.contains(fieldobj))
            if conq and foundfield:
                attq = nt.session.query(Attrib).filter(conq[0])
                for con in conq[1:]:
                    attq = attq.intersect(nt.session.query(Attrib).filter(con))
                attobj = attq.first()

            # Create Attrib if needed
            if not attobj:
                attobj = Attrib(
                    name=groupname,
                    category=taggroup['cat'],
                    fields=fields
                )
            
            # Add Attrib to Article
            if attobj not in artobj.attribs:
                artobj.attribs.append(attobj)
            
            # Add snippets
            if addsnips:
                for sniptxt in taggroup['snippets']:
                    if not sniptxt:
                        continue
                    exsnip = [snipobj for snipobj in artobj.snippets
                        if snipobj.name == groupname 
                        and snipobj.text == sniptxt
                    ]
                    if not any(exsnip):
                        artobj.snippets.append(Snippet(name=groupname, text=sniptxt))
    
    # Save changes
    if commit:
        nt.session.commit()

    # Return tags
    return taggroups

def pdfjoin(pdffile):
    
    # Read PDF
    pdflines = open(pdffile, 'r').readlines()

    # Join PDF lines
    pdftxt = ''
    for line in pdflines:
        if line.endswith('-'):
            pdftxt += line[:-1]
        else:
            pdftxt += line + ' '
    
    # Return
    return pdftxt
    
def parsehtml(html, method='soup'):
    """
    Parse HTML document
    Arguments:
        html (str): Raw HTML text
        method (str): Parsing method (lxml or soup)
    """
    
    if method == 'soup':

        # Parse HTML
        soup = BS(html)

        # Remove <script> elements
        [el.extract() for el in soup.findAll('script')]

        # Assemble text
        txt = ''.join(soup.findAll(text=True))

    elif method == 'lxml':
        
        # Parse HTML
        parse = lxml.html.fromstring(html)

        # Extract text
        txt = parse.text_content()

    # Return text
    return txt

def unique(list):
    
    ulist = []
    for item in list:
        if item not in ulist:
            ulist.append(item)

    return ulist

def procdocs(docs, tags):
    
    tagsum = {}

    for src in tags:

        taglist = []
        snippets = []
        
        # Process documents
        for doc in docs:
            doctags, docsnips = txt2tag(doc, tags[src]['src'], verbose=False)
            taglist.extend(doctags)
            snippets.extend(docsnips)

        # Remove duplicate tags
        taglist = unique(taglist)

        # Remove unversioned tags if versioned tags present
        # Reverse index list to allow deletions
        for tagidx in reversed(range(len(taglist))):
            tag = taglist[tagidx]
            if 'ver' not in tag or not tag['ver']:
                if any([vtag for vtag in taglist if vtag['name'] == tag['name'] and 'ver' in vtag and vtag['ver']]):
                    del(taglist[tagidx])

        # 
        tagsum[src] = {
            'cat' : tags[src]['cat'],
            'tags' : taglist,
            'snippets' : snippets,
        }

    return tagsum

def txt2tag(txt, src, verbose=True):
    
    # Initialize
    taglist = []
    snippets = []
    
    # Apply default clean pattern
    deftxt = cleantxt(txt, repchars)

    for tag in src:
        
        # Check for non-default clean pattern
        if tag in repxcept:
            srctxt = cleantxt(txt, repxcept[tag])
        else:
            srctxt = deftxt
        
        foundtag = False
        foundknownver = False
        foundarbitver = False

        # Get tag pattern
        if type(src[tag]) == dict:
            tagptn = src[tag]['bool']
        elif type(src[tag]) in [list, type(lambda x: x)]:
            tagptn = src[tag]
        else:
            raise Exception('Error: pattern must be a dict, list, or function')

        # Search for tag
        vals = []
        for ptn in tagptn:
            ruleres = ptn.apply(srctxt)
            for val, snip in ruleres:
                if val:
                    foundtag = True
                    vals.append(val)
                    snippets.append(snip)
                    if verbose:
                        print 'Found tag %s with context %s' % (tag, snip)
        
        # Stop if tag not found
        if not foundtag:
            continue
        
        # 
        if type(val) != bool:
            for val in vals:
                if type(val) == dict:
                    val.update({'name' : tag})
                    taglist.append(val)
                else:
                    taglist.append({'name' : tag, 'value' : val})
            continue

        # Stop if no version info
        if type(src[tag]) != dict:
            taglist.append({'name' : tag})
            continue
        
        # Extract version (known)
        for ver in src[tag]:
            if ver in ['bool', 'arbit']:
                continue
            verptn = src[tag][ver]
            for ptn in verptn:
                if any([res[0] for res in ptn.apply(srctxt)]):
                    taglist.append({'name' : tag, 'ver' : ver})
                    foundknownver = True

        # Extract version (arbitrary)
        if not foundknownver:
            if 'arbit' in src[tag]:
                # Initialize arbitrary function with identity
                arbfun = lambda x: x
                if type(src[tag]['arbit']) == dict:
                    arblist = src[tag]['arbit']['src']
                    if 'fun' in src[tag]['arbit']:
                        arbfun = src[tag]['arbit']['fun']
                else:
                    arblist = src[tag]['arbit']
                for arbptn in arblist:
                    arbres = arbptn.apply(srctxt)
                    if arbres:
                        arbver = [res for res in arbres if res][0]
                        if type(arbver) == tuple:
                            arbver = [res for res in arbver if res][0]
                        if arbver:
                            taglist.append({'name' : tag, 'ver' : arbfun(arbver)})
                            foundarbitver = True

        if not (foundknownver or foundarbitver):
            taglist.append({'name' : tag, 'ver' : ''})

    return taglist, snippets
