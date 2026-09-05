"""Tests for Apple Vision result extraction helpers."""

from _apple_vision import _extract_coreml_observation


class FakeClassification:
    def __init__(self, identifier, confidence):
        self._identifier = identifier
        self._confidence = confidence

    def identifier(self):
        return self._identifier

    def confidence(self):
        return self._confidence


class FakeObjectObservation:
    def __init__(self, labels):
        self._labels = labels

    def identifier(self):
        return ""

    def labels(self):
        return self._labels


def test_extracts_coreml_classification_observation():
    observation = FakeClassification("receipt", 0.91)

    assert _extract_coreml_observation(observation) == [("receipt", 0.91)]


def test_extracts_labels_from_coreml_object_observation():
    observation = FakeObjectObservation([
        FakeClassification("document", 0.87),
        FakeClassification("table", 0.63),
    ])

    assert _extract_coreml_observation(observation) == [
        ("document", 0.87),
        ("table", 0.63),
    ]
