#!/usr/bin/env python3

from PyQt5.QtCore import QObject


class Controller(QObject):

    def __init__(self, model):
        super().__init__()

        self._model = model

    # Compute biofeedback.
    def biofeedback(self):
        pass

    # Compute current breathing estimate (spectral ratio).
    def spectral_ratio(self):
        pass
