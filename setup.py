# -*- coding: utf-8 -*-

from Cython.Build import cythonize
from Cython.Distutils import build_ext

from setuptools.extension import Extension
from setuptools import setup, find_packages

import numpy
from pathlib import Path

# Get the readme
with open('README.md') as f:
    readme = f.read()

# Get the licence
with open('LICENSE') as f:
    license = f.read()

# Get the version
mypackage_root_dir = Path(__file__).absolute().parent
with open(mypackage_root_dir / "pysiral" / 'VERSION') as version_file:
    version = version_file.read().strip()

# cythonized extensions go here
extensions = [
    Extension("pysiral.bnfunc.cytfmra", [Path("pysiral/bnfunc/cytfmra.pyx")])]

# Package requirements
with open("requirements.txt") as f:
    requirements = f.read().splitlines()


setup(
    name='pysiral',
    version=version,
    description='python sea ice radar altimetry processing library',
    long_description=readme,
    author='Stefan Hendricks',
    author_email='stefan.hendricks@awi.de',
    url='https://github.com/shendric/pysiral',
    license=license,
    install_requires=requirements,
    packages=find_packages(exclude=('tests', 'docs')),
    include_package_data=True,
    scripts=['bin/psrl_update_userhome_cfg.py', 'bin/pysiral_cfg_setdir.py',
             'bin/pysiral-l1preproc.py',
             'bin/pysiral-l1bpreproc.py', 'bin/pysiral-l1bpreproc.bat',
             'bin/pysiral-l2proc.py', 'bin/pysiral-l2proc.bat',
             'bin/pysiral-l2preproc.py', 'bin/pysiral-l2preproc.bat',
             'bin/pysiral-l3proc.py', 'bin/pysiral-l3proc.bat'],
    cmdclass={'build_ext': build_ext},
    ext_modules=cythonize(extensions),
    include_dirs=[numpy.get_include()]
)