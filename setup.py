import setuptools

TEST_REQUIREMENTS = [
    "Django>=4.2,<5.2",
    "pytest>=8,<9",
    "pytest-django>=4.8,<5",
    "requests-mock>=1.12,<2",
]

QUALITY_REQUIREMENTS = [
    *TEST_REQUIREMENTS,
    "bandit[toml]>=1.8,<2",
    "build>=1.2,<2",
    "coverage[toml]>=7.6,<8",
    "ruff>=0.14,<0.15",
    "setuptools>=69,<81",
    "sphinx>=7.4,<9",
    "sphinx-rtd-theme>=3,<4",
    "twine>=6,<7",
    "wheel>=0.44,<1",
]

with open("README.md", encoding="utf-8") as f:
    long_description = f.read()


setuptools.setup(
    name="django-feed-reader",
    version="2.0.1b5",
    description="An RSS feed reading library for Django.",
    long_description=long_description,
    long_description_content_type="text/markdown",
    author="Gareth Simpson",
    author_email="g@xurble.org",
    url="https://github.com/xurble/django-feed-reader",
    license="MIT",
    packages=setuptools.find_packages(),
    classifiers=[
        "Programming Language :: Python :: 3",
        "License :: OSI Approved :: MIT License",
        "Operating System :: OS Independent",
        "Framework :: Django",
        "Framework :: Django :: 4.2",
        "Framework :: Django :: 5.0",
        "Framework :: Django :: 5.1",
    ],
    install_requires=[
        "dripfeed-client",
        "sgmllib3k",
        "requests",
        "feedparser>=6.0.0",
        "beautifulsoup4",
        "pyrfc3339",
        "Django>=4.2",
    ],
    extras_require={
        "test": TEST_REQUIREMENTS,
        "quality": QUALITY_REQUIREMENTS,
    },
    include_package_data=True,
)
