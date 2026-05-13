"""Tests for the degeneracy predicate used by the diagnostics emitter.

A slot is *degenerate* when, across all observations, its types collapse to
one of:
  - an empty container shape (list[Never], dict[Never, Never], tuple[()], ...);
  - a generator/iterator/coroutine that never advanced past first yield;
  - an Optional value observed only as None.

The predicate operates on the multiset of TypeInfo observations for one
slot and returns the shape name (or None when the slot is *not*
degenerate). The shape name flows directly into the diagnostics JSON.
"""

from __future__ import annotations

import typing
from collections import abc

import pytest

from righttyper.generalize import degenerate_shape
from righttyper.typeinfo import NoneTypeInfo, TypeInfo


NEVER = TypeInfo.from_type(typing.Never)
INT = TypeInfo.from_type(int)
STR = TypeInfo.from_type(str)


def c(t: type, *args: TypeInfo) -> TypeInfo:
    return TypeInfo.from_type(t, args=args)


EMPTY_TUPLE = TypeInfo.from_type(tuple, args=((),))


CASES = [
    # (description, observation set, expected shape-or-None)
    ("list[Never]",                {c(list, NEVER)},                              "empty-container"),
    ("dict[Never, Never]",         {c(dict, NEVER, NEVER)},                       "empty-container"),
    ("set[Never]",                 {c(set, NEVER)},                               "empty-container"),
    ("tuple[()]",                  {EMPTY_TUPLE},                                 "empty-container"),
    ("Generator[None,None,None]",  {c(abc.Generator, NoneTypeInfo, NoneTypeInfo, NoneTypeInfo)}, "never-advanced-generator"),
    ("Iterator[None]",             {c(abc.Iterator, NoneTypeInfo)},               "never-advanced-generator"),
    ("AsyncIterator[None]",        {c(abc.AsyncIterator, NoneTypeInfo)},          "never-advanced-generator"),
    ("AsyncGenerator[None,None]",  {c(abc.AsyncGenerator, NoneTypeInfo, NoneTypeInfo)},          "never-advanced-generator"),
    ("Coroutine[_,_,None]",        {c(abc.Coroutine, NoneTypeInfo, NoneTypeInfo, NoneTypeInfo)}, "never-advanced-generator"),
    ("always None",                {NoneTypeInfo},                                "always-none-optional"),

    # Negative cases.
    ("list[int]",                  {c(list, INT)},                                None),
    ("Generator[int,None,None]",   {c(abc.Generator, INT, NoneTypeInfo, NoneTypeInfo)},          None),
    ("Iterator[int]",              {c(abc.Iterator, INT)},                        None),
    ("Coroutine[_,_,int]",         {c(abc.Coroutine, NoneTypeInfo, NoneTypeInfo, INT)},          None),
    ("list[Never] + list[int]",    {c(list, NEVER), c(list, INT)},                None),
    ("list[Never] + list[str]",    {c(list, NEVER), c(list, STR)},                None),
    ("Iterator[None] + Iterator[int]", {c(abc.Iterator, NoneTypeInfo), c(abc.Iterator, INT)},     None),
    ("tuple[()] + tuple[int]",     {EMPTY_TUPLE, c(tuple, INT)},                  None),
    ("list[Never] + Iterator[None]", {c(list, NEVER), c(abc.Iterator, NoneTypeInfo)},            None),  # mixed degenerate shapes — abstain
    ("None + int",                 {NoneTypeInfo, INT},                           None),
    ("empty observations",         set(),                                         None),
]


@pytest.mark.parametrize("desc,obs,expected", CASES, ids=[row[0] for row in CASES])
def test_degenerate_shape(desc, obs, expected):
    assert degenerate_shape(obs) == expected
