#!/usr/bin/python3
#
# build_changelog - Builds a debian/changelog file from a git commit
# history
# Copyright (C) 2020 Eugenio "g7" Paolantonio <me@medesimo.eu>
# All rights reserved.
#
# Redistribution and use in source and binary forms, with or without
# modification, are permitted provided that the following conditions are met:
#    * Redistributions of source code must retain the above copyright
#      notice, this list of conditions and the following disclaimer.
#    * Redistributions in binary form must reproduce the above copyright
#      notice, this list of conditions and the following disclaimer in the
#      documentation and/or other materials provided with the distribution.
#    * Neither the name of the <organization> nor the
#      names of its contributors may be used to endorse or promote products
#      derived from this software without specific prior written permission.
#
# THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS" AND
# ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE IMPLIED
# WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE
# DISCLAIMED. IN NO EVENT SHALL <COPYRIGHT HOLDER> BE LIABLE FOR ANY
# DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES
# (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES;
# LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED AND
# ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY, OR TORT
# (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE OF THIS
# SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.

import os

import sys

import re

import git

import datetime

import email.utils

import argparse

from collections import OrderedDict, namedtuple

changelog_entry = namedtuple("ChangelogEntry", ["author", "mail", "contents", "date"])

not_allowed_regex = re.compile("[^a-z0-9_]+")

def none_on_exception(*args, **kwargs):
	"""
	Tries to execute a function. If it fails, return None.
	Otherwise, return the function result.

	:param: func: the function to execute
	:param: *args: the args to be passed to the function
	:param: **kwargs: the kwargs to be passed to the function
	"""

	try:
		return func(*args, **kwargs)
	except:
		return None

def sanitize_tag_version(version):
	"""
	Sanitizes a "raw" tag version

	:param: version: the version to sanitize
	"""

	return version.replace("_", "~").replace("%", ":")

def slugify(string):
	"""
	"Slugifies" the supplied string.

	:param: string: the string to slugify
	"""

	return not_allowed_regex.sub(".", string.lower())

def tzinfo_from_offset(offset):
	"""
	Returns a `datetime.timezone` object given
	an offset.

	This based on an answer by 'Turtles Are Cute' on
	stackoverflow: https://stackoverflow.com/a/37097784
	"""

	sign, hours, minutes = re.match('([+\-]?)(\d{2})(\d{2})', str(offset)).groups()
	sign = -1 if sign == '-' else 1
	hours, minutes = int(hours), int(minutes)

	return datetime.timezone(sign * datetime.timedelta(hours=hours, minutes=minutes))

class SlimPackage:

	"""
	A debian/changelog generated on-the-fly from the git history
	of the specified repository.
	"""

	DEBIAN_CHANGELOG_TEMPLATE = \
"""%(name)s (%(version)s) %(release)s; urgency=medium

%(content)s

 -- %(author)s <%(mail)s>  %(date)s\n\n"""

	def __init__(self,
		git_repository,
		commit_hash,
		tag=None,
		tag_prefix="hybris-mobian/",
		branch=None,
		branch_prefix="feature/",
		comment="release"
	):
		"""
		Initialises the class.

		:param: git_repository: an instance of `git.Repo` for the repository
		:param: commit_hash: the upmost commit hash to look at (most probably
		the commit you want to build)
		:param: tag: the tag specifying the version, or None
		:param: tag_prefix: the tag prefix used to find suitable tags.
		Defaults to `hybris-mobian/`.
		:param: branch: the branch we're building on, or None
		:param: branch_prefix: the branch prefix used to define feature branches.
		Defaults to `feature/`
		:param: comment: a comment that will be included in the package version,
		usually the branch slug. Defaults to 'release'

		If `tag` is not specified, the nearest tag is used instead. If no tag
		is found, the latest version of an eventual, old debian/changelog is
		used instead. If no debian/changelog exist, the starting base version will
		be "0.0.0".
		"""

		self.git_repository = git_repository
		self.commit_hash = commit_hash
		self.tag = tag
		self.tag_prefix = tag_prefix
		self.branch = branch
		self.branch_prefix = branch_prefix
		self.comment = slugify(comment.replace(self.branch_prefix, ""))

		self._name = None
		self._is_native = None
		self._version = None
		self._release = None

	def get_version_from_changelog(self):
		"""
		Returns the latest version from debian/changelog, or None
		if nothing has been found.
		"""

		_changelog_path = os.path.join(self.git_repository.working_dir, "debian/changelog")
		if os.path.exists(_changelog_path):
			with open(_changelog_path, "r") as f:
				try:
					return f.readline().split(" ")[1][1:-1]
				except:
					pass

		return None

	@property
	def name(self):
		"""
		Returns the source package name.
		"""

		if self._name is None:
			# Retrieve the source package name from debian/control
			_control_path = os.path.join(self.git_repository.working_dir, "debian/control")

			if os.path.exists(_control_path):
				with open(_control_path, "r") as f:
					# Search for the source definition
					for line in f:
						if line.startswith("Source: "):
							# Here we go!
							self._name = line.strip().split(" ", 1)[-1]
							break

					if self._name is None:
						raise Exception("Unable to determine the source package name!")
			else:
				raise Exception("Unable to find debian/control")

		return self._name

	@property
	def is_native(self):
		"""
		Returns True if the source package is native, False if not.
		"""

		if self._is_native is None:
			# Check debian/source/format
			_source_format_path = os.path.join(self.git_repository.working_dir, "debian/source/format")

			if os.path.exists(_source_format_path):
				with open(_source_format_path, "r") as f:
					_format = f.read().strip()
					self._is_native = not (_format == "3.0 (quilt)")
			else:
				raise Exception("Unable to find debian/source/format")

		return self._is_native

	@property
	def version(self):
		"""
		Returns the package version.

		Version template:
		    %(starting_version)s+git%(timestamp)s.%(short_commit).%(comment)

		If a tag has been specified, that will be used as the `starting_version`.
		Otherwise, the nearest tag is used. If no tag is found and an old
		`debian/changelog` file exists, the starting_version is read from there.
		Failing that, it defaults to "0.0.0".
		"""

		if self._version is not None:
			# Return right now to avoid defining strategies again
			return self._version

		_starting_version_strategies = [
			lambda: self.tag.replace(self.tag_prefix, "").split("/")[-1] if self.tag is not None else None,
			lambda: none_on_exception(
				lambda x, y: x.git.describe("--tags", "--always", "--abbrev=0", "--match=%s*" % y).replace(y,"").split("/")[1],
				self.git_repository,
				self.tag_prefix
			),
			self.get_version_from_changelog,
			lambda: "0.0.0"
		]

		starting_version = None
		for strategy in _starting_version_strategies:
			starting_version = strategy()

			if starting_version is not None:
				break

		self._version = "%s+git%s" % (
			starting_version,
			".".join(
				[
					datetime.datetime.fromtimestamp(
						self.git_repository.commit(rev=self.commit_hash).committed_date
					).strftime("%Y%m%d%H%M%S"),
					self.commit_hash[0:7],
					self.comment
				]
			)
		)

		return self._version

	@property
	def release(self):
		"""
		Returns the target release.
		"""

		if not self._release and self.tag is not None:
			self._release = self.tag.replace(self.tag_prefix, "").split("/")[0]
		elif not self._release and self.branch is not None:
			self._release = self.branch.replace(self.branch_prefix, "").split("/")[0]
		elif not self._release:
			raise Exception("At least one between tag and branch must be specified")

		return self._release

	def iter_changelog(self):
		"""
		Returns a formatted changelog
		"""

		# Keep track of every tag with our prefix
		tags = {
			tag.commit.hexsha : tag.name.replace(self.tag_prefix, "")
			for tag in self.git_repository.tags
			if tag.name.startswith(self.tag_prefix)
		}

		# Use the current release/version pair as the top version
		nearest_version = "%s/%s" % (self.release, self.version)

		entries = OrderedDict()

		####
		entry = None
		for commit in self.git_repository.iter_commits(rev=self.commit_hash):
			if (commit.hexsha in tags and not commit.hexsha == self.commit_hash) \
				or not commit.parents:

				# new version, or root commit, should yield the previous
				release, version = nearest_version.split("/")

				# Store the commit if this is the last one
				if not commit.parents:
					if entry is None:
						# This is an edge case, but I'm not a fan of
						# repeating code - need to do something better here
						entry = changelog_entry(
							author=commit.author.name,
							mail=commit.author.email,
							date=email.utils.format_datetime(
								git.objects.util.from_timestamp(
									commit.committed_date,
									commit.committer_tz_offset
								)
							),
							contents=OrderedDict()
						)

					entry.contents.setdefault(
						commit.author.name,
						[]
					).insert(
						0,
						commit.message.split("\n")[0] # Pick up only the first line
					)

				# Get number of authors
				authors = len(entry.contents)

				yield (
					self.DEBIAN_CHANGELOG_TEMPLATE % {
						"name" : self.name,
						"version" : sanitize_tag_version(version),
						"release" : release,
						"content" : "\n\n".join(
							[
								("  [ %(author)s ]\n%(messages)s" if authors > 1 else "%(messages)s") % {
									"author" : author,
									"messages" : "\n".join(
										[
											"  * %s" % message
											for message in messages
										]
									)
								}
								for author, messages in entry.contents.items()
							]
						),
						"author" : entry.author,
						"mail" : entry.mail,
						"date" : entry.date
					}
				)

				# Reset entry
				entry = None

				# If we should change version, do that
				if commit.parents:
					nearest_version = tags[commit.hexsha]

			# Create entry if we should
			if entry is None:
				entry = changelog_entry(
					author=commit.author.name,
					mail=commit.author.email,
					date=email.utils.format_datetime(
						git.objects.util.from_timestamp(
							commit.committed_date,
							commit.committer_tz_offset
						)
					),
					contents=OrderedDict()
				)

			# Add commit details to the entry
			entry.contents.setdefault(
				commit.author.name,
				[]
			).insert(
				0,
				commit.message.split("\n")[0] # Pick up only the first line
			)

parser = argparse.ArgumentParser(description="Builds a debian/changelog file from a git history tree")
parser.add_argument(
	"--commit",
	type=str,
	help="the commit to search from. Defaults to the current HEAD"
)
parser.add_argument(
	"--git-repository",
	type=str,
	default=os.getcwd(),
	help="the git repository to search on. Defaults to the current directory"
)
parser.add_argument(
	"--tag",
	type=str,
	help="the eventual tag that specifies the base version of the package"
)
parser.add_argument(
	"--tag-prefix",
	type=str,
	default="hybris-mobian/",
	help="the prefix of the tag supplied with --tag. Defaults to hybris-mobian/"
)
parser.add_argument(
	"--branch",
	type=str,
	help="the branch where the commit is on. Defaults to the current branch"
)
parser.add_argument(
	"--branch-prefix",
	type=str,
	default="feature/",
	help="the prefix of the branch supplied with --branch. Defaults to feature/"
)
parser.add_argument(
	"--comment",
	type=str,
	default="release",
	help="a slugified comment that is set as version suffix. Defaults to release"
)

if __name__ == "__main__":
	args = parser.parse_args()

	try:
		repository = git.Repo(args.git_repository, odbt=git.GitCmdObjectDB)
	except:
		raise Exception(
			"Unable to load git repository at %s. You can use --git-repository to change the repo path" % \
				args.git_repository
		)

	pkg = SlimPackage(
		repository,
		commit_hash=args.commit or repository.head.commit.hexsha,
		tag=args.tag,
		tag_prefix=args.tag_prefix,
		branch=args.branch or (None if args.tag else repository.active_branch.name),
		branch_prefix=args.branch_prefix,
		comment=args.comment
	)

	# Build a version right now, so that we don't worry about (eventually)
	# replacing debian/changelog before the get_version_from_changelog
	# strategy is executed
	version = pkg.version
	print("I: Resulting version is %s" % version)

	with open("debian/changelog", "w") as f:
		for entry in pkg.iter_changelog():
			f.write(entry)


