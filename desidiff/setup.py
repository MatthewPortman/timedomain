import setuptools

setuptools.setup(
    name="desidiff", # Replace with your own username
    version="0.0.1",
    author="Alex Kim",
    author_email="agkim@lbl.gov",
    description="A small example package",
    long_description_content_type="text/markdown",
    url="https://github.com/desi/timedomain",
    packages=setuptools.find_packages(),
    classifiers=[
        "Programming Language :: Python :: 3",
        "License :: OSI Approved :: MIT License",
        "Operating System :: OS Independent",
    ],
    python_requires='>=3.6',
)
