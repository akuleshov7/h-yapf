# Copyright 2015 Google Inc. All Rights Reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
"""Calculate the number of blank lines between top-level entities.

Calculates how many blank lines we need between classes, functions, and other
entities at the same level.

  CalculateBlankLines(): the main function exported by this module.

Annotations:
  newlines: The number of newlines required before the node.
"""

from lib2to3 import pytree

from yapf.yapflib import py3compat
from yapf.yapflib import pytree_utils
from yapf.yapflib import pytree_visitor
from yapf.yapflib import style

_NO_BLANK_LINES = 1
_ONE_BLANK_LINE = 2
_TWO_BLANK_LINES = 3

_PYTHON_STATEMENTS = frozenset({
    'small_stmt', 'expr_stmt', 'print_stmt', 'del_stmt', 'pass_stmt',
    'break_stmt', 'continue_stmt', 'return_stmt', 'raise_stmt', 'yield_stmt',
    'import_stmt', 'global_stmt', 'exec_stmt', 'assert_stmt', 'if_stmt',
    'while_stmt', 'for_stmt', 'try_stmt', 'with_stmt', 'nonlocal_stmt',
    'async_stmt', 'simple_stmt'
})


def CalculateBlankLines(tree):
  """Run the blank line calculator visitor over the tree.

  This modifies the tree in place.

  Arguments:
    tree: the top-level pytree node to annotate with subtypes.
  """
  original_blank_lines_calculator = _OriginalBlankLinesCalculator()
  original_blank_lines_calculator.Visit(tree)

  blank_line_calculator = _BlankLineCalculator()
  blank_line_calculator.Visit(tree)


class _OriginalBlankLinesCalculator(pytree_visitor.PyTreeVisitor):
    """ Save the original blacklines.

    Computes how many blanklines there were in the original source file.
    """

    def __init__(self):
        self._level = 0
        self.first_tokens = []

    def Visit(self, node):
        # count the recursion depth in order to perform some final
        # computations at the exit from the root node (i.e. when level == 0)
        #
        self._level += 1
        super().Visit(node)
        self._level -= 1

        if self._level == 0: # the root node
            self._compute_newlines()

    def _compute_newlines(self):
        leaves = sorted(self.first_tokens, key=pytree.Node.get_lineno)

        prev = 1
        for leaf in leaves:
            offset = 0
            if pytree_utils.NodeName(leaf) == 'COMMENT':
                # the lineno of a comment points to the last line
                # of that comment
                offset = leaf.value.count('\n')

            newlines = leaf.get_lineno() - prev - offset

            prev = leaf.get_lineno()
            if pytree_utils.NodeName(leaf) == 'STRING':
                # account for multiline docstrings
                prev += leaf.value.count('\n')

            self._set_original_newlines(leaf, newlines)

    # Skip INDENT, DEDENT, and NEWLINE leaves - they are never (?) the
    # frist token in an unwrapped line.

    def Visit_INDENT(self, node):
        pass

    def Visit_DEDENT(self, node):
        pass

    def Visit_NEWLINE(self, node):
        pass

    def DefaultLeafVisit(self, node):
        if (not self.first_tokens
            or self.first_tokens[-1].get_lineno != node.get_lineno()):
            self.first_tokens.append(node)

    def _set_original_newlines(self, node, n):
        pytree_utils.SetNodeAnnotation(node,
            pytree_utils.Annotation.ORIGINAL_NEWLINES, n)


class _BlankLineCalculator(pytree_visitor.PyTreeVisitor):
  """_BlankLineCalculator - see file-level docstring for a description."""

  def __init__(self):
    self.class_level = 0
    self.function_level = 0
    self.last_comment_lineno = 0
    self.last_was_decorator = False
    self.last_was_class_or_function = False

  def Visit_simple_stmt(self, node):  # pylint: disable=invalid-name
    self.DefaultNodeVisit(node)
    if pytree_utils.NodeName(node.children[0]) == 'COMMENT':
      self.last_comment_lineno = node.children[0].lineno

  def Visit_decorator(self, node):  # pylint: disable=invalid-name
    if (self.last_comment_lineno and
        self.last_comment_lineno == node.children[0].lineno - 1):
      self._SetNumNewlines(node.children[0], _NO_BLANK_LINES)
    else:
      self._SetNumNewlines(node.children[0], self._GetNumNewlines(node))
    for child in node.children:
      self.Visit(child)
    self.last_was_decorator = True

  def Visit_classdef(self, node):  # pylint: disable=invalid-name
    self.last_was_class_or_function = False
    index = self._SetBlankLinesBetweenCommentAndClassFunc(node)
    self.last_was_decorator = False
    self.class_level += 1
    for child in node.children[index:]:
      self.Visit(child)
    self.class_level -= 1
    self.last_was_class_or_function = True

  def Visit_funcdef(self, node):  # pylint: disable=invalid-name
    self.last_was_class_or_function = False
    index = self._SetBlankLinesBetweenCommentAndClassFunc(node)
    if _AsyncFunction(node):
      index = self._SetBlankLinesBetweenCommentAndClassFunc(
          node.prev_sibling.parent)
      self._SetNumNewlines(node.children[0], None)
    else:
      index = self._SetBlankLinesBetweenCommentAndClassFunc(node)
    self.last_was_decorator = False
    self.function_level += 1
    for child in node.children[index:]:
      self.Visit(child)
    self.function_level -= 1
    self.last_was_class_or_function = True

  def DefaultNodeVisit(self, node):
    """Override the default visitor for Node.

    This will set the blank lines required if the last entity was a class or
    function.

    Arguments:
      node: (pytree.Node) The node to visit.
    """
    if self.last_was_class_or_function:
      if pytree_utils.NodeName(node) in _PYTHON_STATEMENTS:
        leaf = pytree_utils.FirstLeafNode(node)
        self._SetNumNewlines(leaf, self._GetNumNewlines(leaf))
    self.last_was_class_or_function = False
    super(_BlankLineCalculator, self).DefaultNodeVisit(node)

  def _SetBlankLinesBetweenCommentAndClassFunc(self, node):
    """Set the number of blanks between a comment and class or func definition.

    Class and function definitions have leading comments as children of the
    classdef and functdef nodes.

    Arguments:
      node: (pytree.Node) The classdef or funcdef node.

    Returns:
      The index of the first child past the comment nodes.
    """
    index = 0
    while pytree_utils.IsCommentStatement(node.children[index]):
      # Standalone comments are wrapped in a simple_stmt node with the comment
      # node as its only child.
      self.Visit(node.children[index].children[0])
      if not self.last_was_decorator:
        self._SetNumNewlines(node.children[index].children[0], _ONE_BLANK_LINE)
      index += 1
    if (index and node.children[index].lineno -
        1 == node.children[index - 1].children[0].lineno):
      self._SetNumNewlines(node.children[index], _NO_BLANK_LINES)
    else:
      if self.last_comment_lineno + 1 == node.children[index].lineno:
        num_newlines = _NO_BLANK_LINES
      else:
        num_newlines = self._GetNumNewlines(node)
      self._SetNumNewlines(node.children[index], num_newlines)
    return index

  def _GetNumNewlines(self, node):
    if self.last_was_decorator:
      return _NO_BLANK_LINES
    elif self._IsTopLevel(node):
      return 1 + style.Get('BLANK_LINES_AROUND_TOP_LEVEL_DEFINITION')
    return _ONE_BLANK_LINE

  def _SetNumNewlines(self, node, num_newlines):
    pytree_utils.SetNodeAnnotation(node, pytree_utils.Annotation.NEWLINES,
                                   num_newlines)

  def _IsTopLevel(self, node):
    # This is added for the sole reason to keep the original behaviour,
    # when comments placed on their own line always had column=0.
    # We store the actual column value now in order to support the
    # SAVE_INITIAL_INDENTS_FORMATTING option.
    #
    def first_leaf_is_comment(node):
        first_leaf = pytree_utils.FirstLeafNode(node)
        return pytree_utils.NodeName(first_leaf) == 'COMMENT'

    return (not (self.class_level or self.function_level) and
            (_StartsInZerothColumn(node) or first_leaf_is_comment(node)))


def _StartsInZerothColumn(node):
  return (pytree_utils.FirstLeafNode(node).column == 0 or
          (_AsyncFunction(node) and node.prev_sibling.column == 0))


def _AsyncFunction(node):
  return (py3compat.PY3 and node.prev_sibling and
          pytree_utils.NodeName(node.prev_sibling) == 'ASYNC')
