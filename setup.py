from setuptools import setup, find_packages

# Setup:
setup(name='pbdlib',
      version='0.1',
      description='Programming by Demonstration module for Python',
      url='',
      author='Emmanuel Pignat',
      author_email='emmanuel.pignat@idiap.ch',
      license='MIT',
      packages=find_packages(),
      install_requires = ['numpy','scipy','matplotlib', 'sklearn', 'dtw', 'jupyter', 'termcolor'],
      # enum was in there, but didn't work in py3
      #install_requires = ['numpy','scipy','matplotlib', 'sklearn', 'dtw', 'enum','jupyter', 'termcolor'],
      zip_safe=False)
