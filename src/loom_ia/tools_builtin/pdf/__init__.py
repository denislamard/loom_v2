# SPDX-License-Identifier: Apache-2.0
"""Outil pdf : écrire un PDF mis en forme d'après ses blocs, et lire le texte d'un PDF.

Le même vocabulaire que ``word`` (``_document``) : le modèle décrit les blocs,
l'outil décide de leur apparence. L'écriture passe par reportlab, la lecture
par pypdf dans un process borné (``worker``) : un PDF vient de n'importe où.

reportlab et pypdf viennent de l'extra ``pdf`` ; ce paquet n'est importé qu'au
montage d'un agent qui active l'outil. Ses paramètres sont dans
``pdf_params``, module léger que la config importe seul.
"""

from loom_ia.tools_builtin.pdf.tool import NAME, PdfTool, build

__all__ = ["NAME", "PdfTool", "build"]
