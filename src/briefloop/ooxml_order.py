"""Place WordprocessingML property children in their schema order.

Word tolerates many out-of-order children, but the OOXML schema (and strict
validators) does not. Appending with OxmlElement puts a child at the end of
its parent; ``insert_ordered`` puts it where ECMA-376 expects it instead.
"""
from docx.oxml import OxmlElement
from docx.oxml.ns import qn

_EDGES = ('top', 'left', 'start', 'bottom', 'right', 'end')
SEQUENCES = {qn('w:' + name): tuple(qn('w:' + tag) for tag in tags) for name, tags in {
    'pPr': ('pStyle', 'keepNext', 'keepLines', 'pageBreakBefore', 'framePr', 'widowControl', 'numPr',
            'suppressLineNumbers', 'pBdr', 'shd', 'tabs', 'suppressAutoHyphens', 'kinsoku', 'wordWrap',
            'overflowPunct', 'topLinePunct', 'autoSpaceDE', 'autoSpaceDN', 'bidi', 'adjustRightInd',
            'snapToGrid', 'spacing', 'ind', 'contextualSpacing', 'mirrorIndents', 'suppressOverlap', 'jc',
            'textDirection', 'textAlignment', 'textboxTightWrap', 'outlineLvl', 'divId', 'cnfStyle', 'rPr',
            'sectPr', 'pPrChange'),
    'rPr': ('ins', 'del', 'moveFrom', 'moveTo', 'rStyle', 'rFonts', 'b', 'bCs', 'i', 'iCs', 'caps',
            'smallCaps', 'strike', 'dstrike', 'outline', 'shadow', 'emboss', 'imprint', 'noProof',
            'snapToGrid', 'vanish', 'webHidden', 'color', 'spacing', 'w', 'kern', 'position', 'sz', 'szCs',
            'highlight', 'u', 'effect', 'bdr', 'shd', 'fitText', 'vertAlign', 'rtl', 'cs', 'em', 'lang',
            'eastAsianLayout', 'specVanish', 'oMath', 'rPrChange'),
    'tblPr': ('tblStyle', 'tblpPr', 'tblOverlap', 'bidiVisual', 'tblStyleRowBandSize', 'tblStyleColBandSize',
              'tblW', 'jc', 'tblCellSpacing', 'tblInd', 'tblBorders', 'shd', 'tblLayout', 'tblCellMar',
              'tblLook', 'tblCaption', 'tblDescription', 'tblPrChange'),
    'tcPr': ('cnfStyle', 'tcW', 'gridSpan', 'hMerge', 'vMerge', 'tcBorders', 'shd', 'noWrap', 'tcMar',
             'textDirection', 'tcFitText', 'vAlign', 'hideMark', 'headers', 'cellIns', 'cellDel', 'cellMerge',
             'tcPrChange'),
    'pBdr': ('top', 'left', 'bottom', 'right', 'between', 'bar'),
    'tblBorders': _EDGES + ('insideH', 'insideV'),
    'tcBorders': _EDGES + ('insideH', 'insideV', 'tl2br', 'tr2bl'),
    'tcMar': _EDGES,
    'tblCellMar': _EDGES,
    'settings': ('writeProtection', 'view', 'zoom', 'removePersonalInformation', 'removeDateAndTime',
                 'doNotDisplayPageBoundaries', 'displayBackgroundShape', 'printPostScriptOverText',
                 'printFractionalCharacterWidth', 'printFormsData', 'embedTrueTypeFonts', 'embedSystemFonts',
                 'saveSubsetFonts', 'saveFormsData', 'mirrorMargins', 'alignBordersAndEdges',
                 'bordersDoNotSurroundHeader', 'bordersDoNotSurroundFooter', 'gutterAtTop', 'hideSpellingErrors',
                 'hideGrammaticalErrors', 'activeWritingStyle', 'proofState', 'formsDesign', 'attachedTemplate',
                 'linkStyles', 'stylePaneFormatFilter', 'stylePaneSortMethod', 'documentType', 'mailMerge',
                 'revisionView', 'trackRevisions', 'doNotTrackMoves', 'doNotTrackFormatting',
                 'documentProtection', 'autoFormatOverride', 'styleLockTheme', 'styleLockQFSet',
                 'defaultTabStop', 'autoHyphenation', 'consecutiveHyphenLimit', 'hyphenationZone',
                 'doNotHyphenateCaps', 'showEnvelope', 'summaryLength', 'clickAndTypeStyle', 'defaultTableStyle',
                 'evenAndOddHeaders', 'bookFoldRevPrinting', 'bookFoldPrinting', 'bookFoldPrintingSheets',
                 'drawingGridHorizontalSpacing', 'drawingGridVerticalSpacing', 'displayHorizontalDrawingGridEvery',
                 'displayVerticalDrawingGridEvery', 'doNotUseMarginsForDrawingGridOrigin',
                 'drawingGridHorizontalOrigin', 'drawingGridVerticalOrigin', 'doNotShadeFormData',
                 'noPunctuationKerning', 'characterSpacingControl', 'printTwoOnOne', 'strictFirstAndLastChars',
                 'noLineBreaksAfter', 'noLineBreaksBefore', 'savePreviewPicture', 'doNotValidateAgainstSchema',
                 'saveInvalidXml', 'ignoreMixedContent', 'alwaysShowPlaceholderText', 'doNotDemarcateInvalidXml',
                 'saveXmlDataOnly', 'useXSLTWhenSaving', 'saveThroughXslt', 'showXMLTags',
                 'alwaysMergeEmptyNamespace', 'updateFields', 'hdrShapeDefaults', 'footnotePr', 'endnotePr',
                 'compat', 'docVars', 'rsids', 'mathPr', 'attachedSchema', 'themeFontLang', 'clrSchemeMapping',
                 'doNotIncludeSubdocsInStats', 'doNotAutoCompressPictures', 'forceUpgrade', 'captions',
                 'readModeInkLockDown', 'smartTagType', 'schemaLibrary', 'shapeDefaults', 'doNotEmbedSmartTags',
                 'decimalSymbol', 'listSeparator'),
}.items()}
# m:mathPr sits in the math namespace but occupies the w:mathPr slot.
_MATH_PR = '{http://schemas.openxmlformats.org/officeDocument/2006/math}mathPr'


def _rank(sequence, tag):
    if tag == _MATH_PR: tag = qn('w:mathPr')
    return sequence.index(tag) if tag in sequence else None


def insert_ordered(parent, child):
    """Insert ``child`` into ``parent`` at its schema position and return it.

    An existing child with the same tag is replaced, since every element these
    sequences cover may occur at most once. Unknown children (extensions) are
    left where they are. Parents without a known sequence fall back to append.
    """
    sequence = SEQUENCES.get(parent.tag)
    rank = _rank(sequence, child.tag) if sequence else None
    if rank is None:
        parent.append(child)
        return child
    for old in parent.findall(child.tag):
        if old is not child: parent.remove(old)
    for existing in parent:
        other = _rank(sequence, existing.tag)
        if existing is not child and other is not None and other > rank:
            existing.addprevious(child)
            return child
    predecessors = [e for e in parent if e is not child and _rank(sequence, e.tag) is not None]
    if predecessors: predecessors[-1].addnext(child)
    else: parent.append(child)
    return child


def add_ordered(parent, tag, **attributes):
    """Create ``w:<tag>`` with ``w:`` attributes and insert it in schema order."""
    node = OxmlElement('w:' + tag)
    for key, value in attributes.items():
        node.set(qn('w:' + key), str(value))
    return insert_ordered(parent, node)


def order_violations(root):
    """Return (parent, child) local-name pairs whose children are out of schema order."""
    problems = []
    for parent in root.iter():
        sequence = SEQUENCES.get(parent.tag)
        if not sequence: continue
        last = -1
        for child in parent:
            if not isinstance(child.tag, str): continue
            rank = _rank(sequence, child.tag)
            if rank is None: continue
            if rank < last:
                problems.append((parent.tag.rsplit('}', 1)[-1], child.tag.rsplit('}', 1)[-1]))
            last = max(last, rank)
    return problems
