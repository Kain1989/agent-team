from textkit import slugify


def test_basic():
    assert slugify("Hello, World!") == "hello-world"


def test_collapses_spaces_and_punctuation():
    assert slugify("  multiple   spaces ") == "multiple-spaces"
    assert slugify("a -- b__c") == "a-b-c"


def test_empty():
    assert slugify("") == ""
    assert slugify("   ") == ""
