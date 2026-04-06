"""
CDS-CityFetch - A CLI tool for fetching city data from Wikidata.
"""

from setuptools import setup, find_packages

with open("README.md", "r", encoding="utf-8") as fh:
    long_description = fh.read()

setup(
    name="cds-cityfetch",
    version="1.0.0",
    author="Filip Dvorak",
    author_email="filip.dvorak13@gmail.com",
    description="Fetch city data from Wikidata and export to multiple formats",
    long_description=long_description,
    long_description_content_type="text/markdown",
    url="https://github.com/filip/cds-cityfetch",
    packages=find_packages(),
    classifiers=[
        "Development Status :: 4 - Beta",
        "Intended Audience :: Developers",
        "Intended Audience :: System Administrators",
        "License :: OSI Approved :: MIT License",
        "Operating System :: OS Independent",
        "Programming Language :: Python :: 3",
        "Programming Language :: Python :: 3.12",
        "Topic :: Database",
        "Topic :: Internet :: WWW/HTTP",
        "Topic :: Utilities",
    ],
    python_requires=">=3.12",
    install_requires=[
        "httpx>=0.27.2",
        "click>=8.1.7",
        "tqdm>=4.66.4",
    ],
    entry_points={
        "console_scripts": [
            "cds-cityfetch=cityfetch.cli:main",
            "cityfetch=cityfetch.cli:main",
        ],
    },
    include_package_data=True,
    zip_safe=False,
)
