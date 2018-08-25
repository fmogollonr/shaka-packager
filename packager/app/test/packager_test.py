#!/usr/bin/python
#
# Copyright 2014 Google Inc. All Rights Reserved.
#
# Use of this source code is governed by a BSD-style
# license that can be found in the LICENSE file or at
# https://developers.google.com/open-source/licenses/bsd
"""Tests utilizing the sample packager binary."""

import filecmp
import glob
import os
import re
import shutil
import subprocess
import tempfile
import unittest

import packager_app
import test_env

_TEST_FAILURE_COMMAND_LINE_MESSAGE = """
!!! To reproduce the failure, change the output files to an !!!
!!! existing directory, e.g. output artifacts to current    !!!
!!! directory by removing /tmp/something/ in the following  !!!
!!! command line.                                           !!!
The test executed the following command line:
""".strip()


class StreamDescriptor(object):
  """Basic class used to build stream descriptor commands."""

  def __init__(self, input_file):
    self._buffer = 'input=%s' % input_file
    self._output_file_name_base = os.path.splitext(
        os.path.basename(input_file))[0]

  def Append(self, key, value):
    self._buffer += ',%s=%s' % (key, value)

    # Generate an unique |_output_file_name_base| from some of the keys.
    # We do not need all the keys as it is sufficient with the below keys.
    if key == 'stream':
      self._output_file_name_base += '-%s' % value
    elif key == 'trick_play_factor':
      self._output_file_name_base += '-trick_play_factor_%d' % value
    elif key == 'skip_encryption':
      self._output_file_name_base += '-skip_encryption'

    return self

  def GetOutputFileNameBase(self, output_file_prefix):
    if output_file_prefix:
      return '%s-%s' % (output_file_prefix, self._output_file_name_base)
    else:
      return self._output_file_name_base

  def __str__(self):
    return self._buffer


class DiffFilesPolicy(object):
  """Class for handling files comparison.

  Attributes:
    _allowed_diff_files: The list of files allowed to be different.
    _exact: The actual list of diff_files must match the above list exactly,
        i.e. all the files in the above list must be different.
    _allow_updating_golden_files: When set to false, golden files will not be
        updated for this test even if updating_golden_files is requested. This
        is useful for tests generating different outputs in each run, which is
        often used together when _allowed_diff_files is not empty.
  """

  def __init__(self,
               allowed_diff_files=None,
               exact=True,
               allow_updating_golden_files=True):
    if allowed_diff_files:
      self._allowed_diff_files = allowed_diff_files
    else:
      self._allowed_diff_files = []
    self._exact = exact
    self._allow_updating_golden_files = allow_updating_golden_files

  def ProcessDiff(self, out_dir, gold_dir):
    """Compare test outputs with golden files.

    Args:
      out_dir: The test output directory.
      gold_dir: The golden directory to be compared with.
    Returns:
      A list of diff messages when the files do not match; An empty list
      otherwise or in update mode.
    """
    if test_env.options.test_update_golden_files:
      if self._allow_updating_golden_files:
        self._UpdateGold(out_dir, gold_dir)
      return []
    else:
      return self._DiffDir(out_dir, gold_dir)

  def _DiffDir(self, out_dir, gold_dir):
    # Get a list of the files and dirs that are different between the two top
    # level directories.
    diff = filecmp.dircmp(out_dir, gold_dir)

    # Create a list of all the details about the failure. The list will be
    # joined together when sent out.
    failure_messages = []

    missing = diff.left_only
    if missing:
      failure_messages += [
          'Missing %d files: %s' % (len(missing), str(missing))
      ]

    extra = diff.right_only
    if extra:
      failure_messages += [
          'Found %d unexpected files: %s' % (len(extra), str(extra))
      ]

    # Produce nice diffs for each file that differs.
    for diff_file in diff.diff_files:
      if diff_file in self._allowed_diff_files:
        continue

      actual_file = os.path.join(out_dir, diff_file)
      expected_file = os.path.join(gold_dir, diff_file)

      output, error = self._GitDiff(expected_file, actual_file)

      if output:
        failure_messages += [output]

      if error:
        failure_messages += [error]

    if self._exact:
      for diff_file in self._allowed_diff_files:
        if diff_file not in diff.diff_files:
          failure_messages += ['Expecting "%s" to be different' % diff_file]

    return failure_messages

  def _GitDiff(self, file_a, file_b):
    cmd = [
        'git',
        '--no-pager',
        'diff',
        '--color=auto',
        '--no-ext-diff',
        '--no-index',
        file_a,
        file_b
    ]
    p = subprocess.Popen(cmd, stderr=subprocess.PIPE, stdout=subprocess.PIPE)
    return p.communicate()

  def _UpdateGold(self, out_dir, gold_dir):
    if os.path.exists(gold_dir):
      shutil.rmtree(gold_dir)

    shutil.copytree(out_dir, gold_dir)


def _UpdateMediaInfoPaths(media_info_filepath):
  # Example:
  #   before: media_file_name: "/tmp/tmpD1h5UC/bear-640x360-audio.mp4"
  #   after:  media_file_name: "bear-640x360-audio.mp4"

  with open(media_info_filepath, 'rb') as f:
    content = f.read()

  regex = 'media_file_name: "(.*)"'
  for path in re.findall(regex, content):
    short_path = os.path.basename(path)
    content = content.replace(path, short_path)

  with open(media_info_filepath, 'wb') as f:
    f.write(content)


def _UpdateMpdTimes(mpd_filepath):
  # Take a single pattern, and replace the first match with the
  # given new string.
  def _Replace(str_in, pattern, new):
    m = re.search(pattern, str_in)

    if m:
      old = m.group(0)
      out = str_in.replace(old, new)
      print 'Replacing "%s" with "%s"' % (old, new)
    else:
      out = str_in

    return out

  with open(mpd_filepath, 'rb') as f:
    content = f.read()

  content = _Replace(
      content,
      'availabilityStartTime="[^"]+"',
      'availabilityStartTime="some_time"')

  content = _Replace(
      content,
      'publishTime="[^"]+"',
      'publishTime="some_time"')

  with open(mpd_filepath, 'wb') as f:
    f.write(content)


def GetExtension(stream_descriptor, output_format):
  if output_format:
    return output_format
  # TODO(rkuroiwa): Support ttml.
  if stream_descriptor == 'text':
    return 'vtt'
  # Default to mp4.
  return 'mp4'


def GetSegmentedExtension(base_extension):
  if base_extension == 'mp4':
    return 'm4s'

  return base_extension


class PackagerAppTest(unittest.TestCase):

  def setUp(self):
    self.packager = packager_app.PackagerApp()
    self.tmp_dir = tempfile.mkdtemp()
    self.test_data_dir = os.path.join(test_env.SRC_DIR, 'packager', 'media',
                                      'test', 'data')
    self.golden_file_dir = os.path.join(test_env.SRC_DIR, 'packager', 'app',
                                        'test', 'testdata')
    self.mpd_output = os.path.join(self.tmp_dir, 'output.mpd')
    self.hls_master_playlist_output = os.path.join(self.tmp_dir, 'output.m3u8')
    self.output = []

    # Test variables.
    self.encryption_key_id = '31323334353637383930313233343536'
    if test_env.options.encryption_key:
      self.encryption_key = test_env.options.encryption_key
    else:
      self.encryption_key = '32333435363738393021323334353637'
    if test_env.options.encryption_iv:
      self.encryption_iv = test_env.options.encryption_iv
    else:
      self.encryption_iv = '3334353637383930'
    self.widevine_content_id = '3031323334353637'
    # TS files may have a non-zero start, which could result in the first
    # segment to be less than 1 second. Set clear_lead to be less than 1
    # so only the first segment is left in clear.
    self.clear_lead = 0.8

  def tearDown(self):
    if test_env.options.remove_temp_files_after_test:
      shutil.rmtree(self.tmp_dir)

  def _GetStream(self,
                 descriptor,
                 language=None,
                 output_file_prefix=None,
                 output_format=None,
                 segmented=False,
                 hls=False,
                 trick_play_factor=None,
                 drm_label=None,
                 skip_encryption=None,
                 bandwidth=None,
                 split_content_on_ad_cues=False,
                 test_file=None):
    """Get a stream descriptor as a string.


    Create the stream descriptor as a string for the given parameters so that
    it can be passed as an input parameter to the packager.


    Args:
      descriptor: The name of the stream in the container that should be used as
          input for the output.
      language: The language override for the input stream.
      output_file_prefix: The output file prefix. Default to empty if not
          specified.
      output_format: Specify the format for the output.
      segmented: Should the output use a segmented formatted. This will affect
          the output extensions and manifests.
      hls: Should the output be for an HLS manifest.
      trick_play_factor: Signals the stream is to be used for a trick play
          stream and which key frames to use. A trick play factor of 0 is the
          same as not specifying a trick play factor.
      drm_label: Sets the drm label for the stream.
      skip_encryption: If set to true, the stream will not be encrypted.
      bandwidth: The expected bandwidth value that should be listed in the
          manifest.
      split_content_on_ad_cues: If set to true, the output file will be split
          into multiple files, with a total of NumAdCues + 1 files.
      test_file: Specify the input file to use. If the input file is not
          specify, a default file will be used.


    Returns:
      A string that makes up a single stream descriptor for input to the
      packager.
    """
    input_file_name = test_file or 'bear-640x360.mp4'
    input_file_path = os.path.join(self.test_data_dir, input_file_name)

    stream = StreamDescriptor(input_file_path)
    stream.Append('stream', descriptor)

    if output_format:
      stream.Append('format', output_format)

    if language:
      stream.Append('lang', language)

    if trick_play_factor:
      stream.Append('trick_play_factor', trick_play_factor)

    if drm_label:
      stream.Append('drm_label', drm_label)

    if skip_encryption:
      stream.Append('skip_encryption', 1)

    base_ext = GetExtension(descriptor, output_format)
    output_file_name_base = stream.GetOutputFileNameBase(output_file_prefix)

    if hls:
      stream.Append('playlist_name', output_file_name_base + '.m3u8')

      # By default, add a iframe playlist for all HLS playlists (assuming that
      # the source input is supported). iframe playlists should only be for
      # videos. This check will fail for numeric descriptors, but that is an
      # acceptable limitation (b/73960731).
      if base_ext in ['ts', 'mp4'] and descriptor == 'video':
        stream.Append('iframe_playlist_name',
                      output_file_name_base + '-iframe.m3u8')

    requires_init_segment = segmented and base_ext not in [
        'aac', 'ac3', 'ec3', 'ts', 'vtt'
    ]

    output_file_path = os.path.join(self.tmp_dir, output_file_name_base)

    if requires_init_segment:
      init_seg = '%s-init.%s' % (output_file_path, base_ext)
      stream.Append('init_segment', init_seg)

    if segmented:
      segment_ext = GetSegmentedExtension(base_ext)
      seg_template = '%s-$Number$.%s' % (output_file_path, segment_ext)
      stream.Append('segment_template', seg_template)
    else:
      if split_content_on_ad_cues:
        output_file_path += '$Number$.' +  base_ext
      else:
        output_file_path += '.' +  base_ext
      stream.Append('output', output_file_path)

    if bandwidth:
      stream.Append('bandwidth', bandwidth)

    self.output.append(output_file_path)

    return str(stream)

  def _GetStreams(self, streams, test_files=None, **kwargs):
    # Make sure there is a valid list that we can get the length from.
    test_files = test_files or []
    test_files_count = len(test_files)

    out = []

    if test_files_count == 0:
      for stream in streams:
        out.append(self._GetStream(stream, **kwargs))
    else:
      for file_name in test_files:
        for stream in streams:
          out.append(self._GetStream(stream, test_file=file_name, **kwargs))

    return out

  def _GetFlags(self,
                strip_parameter_set_nalus=True,
                encryption=False,
                fairplay=False,
                protection_scheme=None,
                vp9_subsample_encryption=True,
                decryption=False,
                random_iv=False,
                widevine_encryption=False,
                key_rotation=False,
                include_pssh_in_stream=True,
                dash_if_iop=True,
                output_media_info=False,
                output_dash=False,
                output_hls=False,
                hls_playlist_type=None,
                time_shift_buffer_depth=0.0,
                preserved_segments_outside_live_window=0,
                utc_timings=None,
                generate_static_mpd=False,
                ad_cues=None,
                default_language=None,
                segment_duration=1.0,
                use_fake_clock=True):
    flags = []

    if not strip_parameter_set_nalus:
      flags += ['--strip_parameter_set_nalus=false']

    if widevine_encryption:
      widevine_server_url = ('https://license.uat.widevine.com/cenc'
                             '/getcontentkey/widevine_test')
      flags += [
          '--enable_widevine_encryption',
          '--key_server_url=' + widevine_server_url,
          '--content_id=' + self.widevine_content_id,
      ]
    elif encryption:
      flags += [
          '--enable_raw_key_encryption',
          '--keys=label=:key_id={0}:key={1}'.format(self.encryption_key_id,
                                                    self.encryption_key),
          '--clear_lead={0}'.format(self.clear_lead)
      ]

      if not random_iv:
        flags.append('--iv=' + self.encryption_iv)

      if fairplay:
        fairplay_key_uri = ('skd://www.license.com/'
                            'getkey?KeyId=31323334-3536-3738-3930-313233343536')
        flags += [
            '--protection_systems=FairPlay', '--hls_key_uri=' + fairplay_key_uri
        ]
    if protection_scheme:
      flags += ['--protection_scheme', protection_scheme]
    if not vp9_subsample_encryption:
      flags += ['--vp9_subsample_encryption=false']

    if decryption:
      flags += [
          '--enable_raw_key_decryption',
          '--keys=label=:key_id={0}:key={1}'.format(self.encryption_key_id,
                                                    self.encryption_key)
      ]

    if key_rotation:
      flags.append('--crypto_period_duration=1')

    if not include_pssh_in_stream:
      flags.append('--mp4_include_pssh_in_stream=false')

    if not dash_if_iop:
      flags.append('--generate_dash_if_iop_compliant_mpd=false')

    if output_media_info:
      flags.append('--output_media_info')
    if output_dash:
      flags += ['--mpd_output', self.mpd_output]
    if output_hls:
      flags += ['--hls_master_playlist_output', self.hls_master_playlist_output]
      if hls_playlist_type:
        flags += ['--hls_playlist_type', hls_playlist_type]

    if time_shift_buffer_depth != 0.0:
      flags += ['--time_shift_buffer_depth={0}'.format(time_shift_buffer_depth)]
    if preserved_segments_outside_live_window != 0:
      flags += [
          '--preserved_segments_outside_live_window={0}'.format(
              preserved_segments_outside_live_window)
      ]

    if utc_timings:
      flags += ['--utc_timings', utc_timings]

    if generate_static_mpd:
      flags += ['--generate_static_mpd']

    if ad_cues:
      flags += ['--ad_cues', ad_cues]

    if default_language:
      flags += ['--default_language', default_language]

    flags.append('--segment_duration={0}'.format(segment_duration))

    # Use fake clock, so output can be compared.
    if use_fake_clock:
      flags.append('--use_fake_clock_for_muxer')

    # Override packager version string for testing.
    flags += ['--test_packager_version', '<tag>-<hash>-<test>']
    return flags

  def _AssertStreamInfo(self, stream, info):
    stream_info = self.packager.DumpStreamInfo(stream)
    self.assertIn('Found 1 stream(s).', stream_info)
    self.assertIn(info, stream_info)

  def _Decrypt(self, file_path, output_format):
    streams = [
        self._GetStream(
            '0',
            output_file_prefix='decrypted',
            output_format=output_format,
            test_file=file_path)
    ]
    self.assertPackageSuccess(streams, self._GetFlags(decryption=True))

  def _CheckTestResults(self,
                        test_dir,
                        verify_decryption=False,
                        diff_files_policy=DiffFilesPolicy()):
    """Check test results. Updates golden files in update mode.

    Args:
      test_dir: The golden directory to be compared with. It is expected to be
          relative to |self.golden_file_dir|.
      verify_decryption: If set to true, assumes the media files without
          'skip-encryption' in name to be encrypted and tries to decrypt and
          then compare these files.
      diff_files_policy: Specifies DiffFiles policy and handles files
          comparison.
    """
    # Live mpd contains current availabilityStartTime and publishTime, which
    # needs to be replaced before comparison. If this is not a live test, then
    # this will be a no-op.
    mpds = glob.glob(os.path.join(self.tmp_dir, '*.mpd'))
    for manifest in mpds:
      _UpdateMpdTimes(manifest)

    # '*.media_info' outputs contain media file names, which is changing for
    # every test run. These needs to be replaced for comparison.
    media_infos = glob.glob(os.path.join(self.tmp_dir, '*.media_info'))
    for media_info in media_infos:
      _UpdateMediaInfoPaths(media_info)

    if verify_decryption:
      for file_name in os.listdir(self.tmp_dir):
        if 'skip_encryption' in file_name:
          continue
        extension = os.path.splitext(file_name)[1][1:]
        if extension not in ['mpd', 'm3u8', 'media_info']:
          self._Decrypt(os.path.join(self.tmp_dir, file_name), extension)

    out_dir = self.tmp_dir
    gold_dir = os.path.join(self.golden_file_dir, test_dir)
    failure_messages = diff_files_policy.ProcessDiff(out_dir, gold_dir)
    if failure_messages:
      # Prepend the failure messages with the header.
      failure_messages = [
          _TEST_FAILURE_COMMAND_LINE_MESSAGE,
          self.packager.GetCommandLine()
      ] + failure_messages

      self.fail('\n'.join(failure_messages))


class PackagerFunctionalTest(PackagerAppTest):

  def assertPackageSuccess(self, streams, flags=None):
    self.assertEqual(self.packager.Package(streams, flags), 0)

  def assertMpdGeneratorSuccess(self):
    media_infos = glob.glob(os.path.join(self.tmp_dir, '*.media_info'))
    self.assertTrue(media_infos)

    flags = ['--input', ','.join(media_infos), '--output', self.mpd_output]
    flags += ['--test_packager_version', '<tag>-<hash>-<test>']
    self.assertEqual(self.packager.MpdGenerator(flags), 0)

  def testVersion(self):
    self.assertRegexpMatches(
        self.packager.Version(), '^packager(.exe)? version '
        r'((?P<tag>[\w\.]+)-)?(?P<hash>[a-f\d]+)-(debug|release)[\r\n]+.*$')

  def testDumpStreamInfo(self):
    test_file = os.path.join(self.test_data_dir, 'bear-640x360.mp4')
    stream_info = self.packager.DumpStreamInfo(test_file)
    expected_stream_info = ('Found 2 stream(s).\n'
                            'Stream [0] type: Video\n'
                            ' codec_string: avc1.64001e\n'
                            ' time_scale: 30000\n'
                            ' duration: 82082 (2.7 seconds)\n'
                            ' is_encrypted: false\n'
                            ' codec: H264\n'
                            ' width: 640\n'
                            ' height: 360\n'
                            ' pixel_aspect_ratio: 1:1\n'
                            ' trick_play_factor: 0\n'
                            ' nalu_length_size: 4\n\n'
                            'Stream [1] type: Audio\n'
                            ' codec_string: mp4a.40.2\n'
                            ' time_scale: 44100\n'
                            ' duration: 121856 (2.8 seconds)\n'
                            ' is_encrypted: false\n'
                            ' codec: AAC\n'
                            ' sample_bits: 16\n'
                            ' num_channels: 2\n'
                            ' sampling_frequency: 44100\n'
                            ' language: und\n')
    stream_info = stream_info.replace('\r\n', '\n')
    self.assertIn(expected_stream_info, stream_info,
                  '\nExpecting: \n %s\n\nBut seeing: \n%s' %
                  (expected_stream_info, stream_info))

  def testFirstStream(self):
    self.assertPackageSuccess(
        self._GetStreams(['0']), self._GetFlags(output_dash=True))
    self._CheckTestResults('first-stream')

  def testText(self):
    self.assertPackageSuccess(
        self._GetStreams(['text'], test_files=['subtitle-english.vtt']),
        self._GetFlags(output_dash=True))
    self._CheckTestResults('text')

  # Probably one of the most common scenarios is to package audio and video.
  def testAudioVideo(self):
    self.assertPackageSuccess(
        self._GetStreams(['audio', 'video']), self._GetFlags(output_dash=True))
    self._CheckTestResults('audio-video')

  def testAudioVideoWithTrickPlay(self):
    streams = [
        self._GetStream('audio'),
        self._GetStream('video'),
        self._GetStream('video', trick_play_factor=1),
    ]

    self.assertPackageSuccess(streams, self._GetFlags(output_dash=True))
    self._CheckTestResults('audio-video-with-trick-play')

  def testAudioVideoWithTwoTrickPlay(self):
    streams = [
        self._GetStream('audio'),
        self._GetStream('video'),
        self._GetStream('video', trick_play_factor=1),
        self._GetStream('video', trick_play_factor=2),
    ]

    self.assertPackageSuccess(streams, self._GetFlags(output_dash=True))
    self._CheckTestResults('audio-video-with-two-trick-play')

  def testAudioVideoWithTwoTrickPlayDecreasingRate(self):
    streams = [
        self._GetStream('audio'),
        self._GetStream('video'),
        self._GetStream('video', trick_play_factor=2),
        self._GetStream('video', trick_play_factor=1),
    ]

    self.assertPackageSuccess(streams, self._GetFlags(output_dash=True))
    # Since the stream descriptors are sorted in packager app, a different
    # order of trick play factors gets the same mpd.
    self._CheckTestResults('audio-video-with-two-trick-play')

  def testAudioVideoWithLanguageOverride(self):
    self.assertPackageSuccess(
        self._GetStreams(['audio', 'video'], language='por', hls=True),
        self._GetFlags(default_language='por', output_dash=True,
                       output_hls=True))
    self._CheckTestResults('audio-video-with-language-override')

  def testAudioVideoWithLanguageOverrideUsingMixingCode(self):
    self.assertPackageSuccess(
        self._GetStreams(['audio', 'video'], language='por', hls=True),
        self._GetFlags(default_language='pt', output_dash=True,
                       output_hls=True))
    self._CheckTestResults('audio-video-with-language-override')

  def testAudioVideoWithLanguageOverrideUsingMixingCode2(self):
    self.assertPackageSuccess(
        self._GetStreams(['audio', 'video'], language='pt', hls=True),
        self._GetFlags(default_language='por', output_dash=True,
                       output_hls=True))
    self._CheckTestResults('audio-video-with-language-override')

  def testAudioVideoWithLanguageOverrideUsingTwoCharacterCode(self):
    self.assertPackageSuccess(
        self._GetStreams(['audio', 'video'], language='pt', hls=True),
        self._GetFlags(default_language='pt', output_dash=True,
                       output_hls=True))
    self._CheckTestResults('audio-video-with-language-override')

  def testAudioVideoWithLanguageOverrideWithSubtag(self):
    self.assertPackageSuccess(
        self._GetStreams(['audio', 'video'], language='por-BR', hls=True),
        self._GetFlags(output_dash=True, output_hls=True))
    self._CheckTestResults('audio-video-with-language-override-with-subtag')

  def testAacHe(self):
    self.assertPackageSuccess(
        self._GetStreams(
            ['audio'], test_files=['bear-640x360-aac_he-silent_right.mp4']),
        self._GetFlags(output_dash=True))
    self._CheckTestResults('acc-he')

  # Package all video, audio, and text.
  def testVideoAudioText(self):
    audio_video_streams = self._GetStreams(['audio', 'video'])
    text_stream = self._GetStreams(['text'],
                                   test_files=['subtitle-english.vtt'])
    self.assertPackageSuccess(audio_video_streams + text_stream,
                              self._GetFlags(output_dash=True))
    self._CheckTestResults('video-audio-text')

  def testVideoNoEditList(self):
    stream = self._GetStream('video', test_file='bear-640x360-no_edit_list.mp4')
    self.assertPackageSuccess([stream], self._GetFlags(output_dash=True))
    self._CheckTestResults('video-no-edit-list')

  def testAvcAacTs(self):
    # Currently we only support live packaging for ts.
    self.assertPackageSuccess(
        self._GetStreams(
            ['audio', 'video'],
            output_format='ts',
            segmented=True,
            hls=True,
            test_files=['bear-640x360.ts']),
        self._GetFlags(output_dash=True, output_hls=True))
    self._CheckTestResults('avc-aac-ts')

  def testAvcAc3Ts(self):
    # Currently we only support live packaging for ts.
    self.assertPackageSuccess(
        self._GetStreams(
            ['audio', 'video'],
            output_format='ts',
            segmented=True,
            hls=True,
            test_files=['bear-640x360-ac3.ts']),
        self._GetFlags(output_hls=True))
    self._CheckTestResults('avc-ac3-ts')

  def testAvcAc3TsToMp4(self):
    self.assertPackageSuccess(
        self._GetStreams(
            ['audio', 'video'], hls=True, test_files=['bear-640x360-ac3.ts']),
        self._GetFlags(output_hls=True))
    self._CheckTestResults('avc-ac3-ts-to-mp4')

  def testAvcTsLivePlaylist(self):
    self.assertPackageSuccess(
        self._GetStreams(
            ['audio', 'video'],
            output_format='ts',
            segmented=True,
            hls=True,
            test_files=['bear-640x360.ts']),
        self._GetFlags(
            output_hls=True,
            hls_playlist_type='LIVE',
            time_shift_buffer_depth=0.5))
    self._CheckTestResults('avc-ts-live-playlist')

  def testAvcTsLivePlaylistWithKeyRotation(self):
    self.packager.Package(
        self._GetStreams(
            ['audio', 'video'],
            output_format='ts',
            segmented=True,
            hls=True,
            test_files=['bear-640x360.ts']),
        self._GetFlags(
            encryption=True,
            key_rotation=True,
            output_hls=True,
            hls_playlist_type='LIVE',
            time_shift_buffer_depth=0.5))
    self._CheckTestResults('avc-ts-live-playlist-with-key-rotation')

  def testAvcTsEventPlaylist(self):
    self.assertPackageSuccess(
        self._GetStreams(
            ['audio', 'video'],
            output_format='ts',
            segmented=True,
            hls=True,
            test_files=['bear-640x360.ts']),
        self._GetFlags(
            output_hls=True,
            hls_playlist_type='EVENT',
            time_shift_buffer_depth=0.5))
    self._CheckTestResults('avc-ts-event-playlist')

  def testAvcTsLivePlaylistAndDashDynamicWithSegmentDeletion(self):
    self.assertPackageSuccess(
        self._GetStreams(
            ['audio'],
            output_format='mp4',
            segmented=True,
            hls=True,
            test_files=['bear-640x360.ts']),
        self._GetFlags(
            output_hls=True,
            hls_playlist_type='LIVE',
            output_dash=True,
            segment_duration=0.5,
            time_shift_buffer_depth=0.5,
            preserved_segments_outside_live_window=1))
    self._CheckTestResults(
        'avc-ts-live-playlist-dash-dynamic-with-segment-deletion')

  def testVp8Webm(self):
    self.assertPackageSuccess(
        self._GetStreams(['video'],
                         output_format='webm',
                         test_files=['bear-640x360.webm']),
        self._GetFlags(output_dash=True))
    self._CheckTestResults('vp8-webm')

  def testVp9Webm(self):
    self.assertPackageSuccess(
        self._GetStreams(['audio', 'video'],
                         output_format='webm',
                         test_files=['bear-320x240-vp9-opus.webm']),
        self._GetFlags(output_dash=True))
    self._CheckTestResults('vp9-webm')

  def testVp9WebmWithBlockgroup(self):
    self.assertPackageSuccess(
        self._GetStreams(['video'],
                         output_format='webm',
                         test_files=['bear-vp9-blockgroup.webm']),
        self._GetFlags(output_dash=True))
    self._CheckTestResults('vp9-webm-with-blockgroup')

  def testVorbisWebm(self):
    self.assertPackageSuccess(
        self._GetStreams(['audio'],
                         output_format='webm',
                         test_files=['bear-320x240-audio-only.webm']),
        self._GetFlags(output_dash=True))
    self._CheckTestResults('vorbis-webm')

  def testEncryption(self):
    self.assertPackageSuccess(
        self._GetStreams(['audio', 'video']),
        self._GetFlags(encryption=True, output_dash=True))
    self._CheckTestResults('encryption', verify_decryption=True)

  # Test deprecated flag --enable_fixed_key_encryption, which is still
  # supported currently.
  def testEncryptionUsingFixedKey(self):
    flags = self._GetFlags(output_dash=True) + [
        '--enable_fixed_key_encryption', '--key_id={0}'.format(
            self.encryption_key_id), '--key={0}'.format(self.encryption_key),
        '--clear_lead={0}'.format(self.clear_lead), '--iv={0}'.format(
            self.encryption_iv)
    ]
    self.assertPackageSuccess(self._GetStreams(['audio', 'video']), flags)
    self._CheckTestResults('encryption-using-fixed-key', verify_decryption=True)

  def testEncryptionMultiKeys(self):
    audio_key_id = '10111213141516171819202122232425'
    audio_key = '11121314151617181920212223242526'
    video_key_id = '20212223242526272829303132333435'
    video_key = '21222324252627282930313233343536'
    flags = self._GetFlags(output_dash=True) + [
        '--enable_raw_key_encryption',
        '--keys=label=AUDIO:key_id={0}:key={1},label=SD:key_id={2}:key={3}'.
        format(audio_key_id, audio_key,
               video_key_id, video_key), '--clear_lead={0}'.format(
                   self.clear_lead), '--iv={0}'.format(self.encryption_iv)
    ]
    self.assertPackageSuccess(self._GetStreams(['audio', 'video']), flags)
    self._CheckTestResults('encryption-multi-keys')

  def testEncryptionMultiKeysWithStreamLabel(self):
    audio_key_id = '20212223242526272829303132333435'
    audio_key = '21222324252627282930313233343536'
    video_key_id = '10111213141516171819202122232425'
    video_key = '11121314151617181920212223242526'
    flags = self._GetFlags(output_dash=True) + [
        '--enable_raw_key_encryption',
        '--keys=label=MyAudio:key_id={0}:key={1},label=:key_id={2}:key={3}'.
        format(audio_key_id, audio_key,
               video_key_id, video_key), '--clear_lead={0}'.format(
                   self.clear_lead), '--iv={0}'.format(self.encryption_iv)
    ]
    # DRM label 'MyVideo' is not defined, will fall back to the key for the
    # empty default label.
    streams = [
        self._GetStream('audio', drm_label='MyAudio'),
        self._GetStream('video', drm_label='MyVideo')
    ]

    self.assertPackageSuccess(streams, flags)
    self._CheckTestResults('encryption-multi-keys-with-stream-label')

  def testEncryptionOfOnlyVideoStream(self):
    streams = [
        self._GetStream('audio', skip_encryption=True),
        self._GetStream('video')
    ]
    flags = self._GetFlags(encryption=True, output_dash=True)

    self.assertPackageSuccess(streams, flags)
    self._CheckTestResults(
        'encryption-of-only-video-stream', verify_decryption=True)

  def testEncryptionAndTrickPlay(self):
    streams = [
        self._GetStream('audio'),
        self._GetStream('video'),
        self._GetStream('video', trick_play_factor=1),
    ]

    self.assertPackageSuccess(streams,
                              self._GetFlags(encryption=True, output_dash=True))
    self._CheckTestResults('encryption-and-trick-play', verify_decryption=True)

  # TODO(hmchen): Add a test case that SD and HD AdapatationSet share one trick
  # play stream.
  def testEncryptionAndTwoTrickPlays(self):
    streams = [
        self._GetStream('audio'),
        self._GetStream('video'),
        self._GetStream('video', trick_play_factor=1),
        self._GetStream('video', trick_play_factor=2),
    ]

    self.assertPackageSuccess(streams,
                              self._GetFlags(encryption=True, output_dash=True))
    self._CheckTestResults('encryption-and-two-trick-plays')

  def testEncryptionAndNoClearLead(self):
    streams = [
        self._GetStream('audio'),
        self._GetStream('video')
    ]

    self.clear_lead = 0
    self.assertPackageSuccess(streams,
                              self._GetFlags(encryption=True, output_dash=True))
    self._CheckTestResults(
        'encryption-and-no-clear-lead', verify_decryption=True)

  def testEncryptionAndNoPsshInStream(self):
    self.assertPackageSuccess(
        self._GetStreams(['audio', 'video']),
        self._GetFlags(
            encryption=True, include_pssh_in_stream=False, output_dash=True))
    self._CheckTestResults('encryption-and-no-pssh-in-stream')

  def testEncryptionCbc1(self):
    self.assertPackageSuccess(
        self._GetStreams(['audio', 'video']),
        self._GetFlags(
            encryption=True, protection_scheme='cbc1', output_dash=True))
    self._CheckTestResults('encryption-cbc-1', verify_decryption=True)

  def testEncryptionCens(self):
    self.assertPackageSuccess(
        self._GetStreams(['audio', 'video']),
        self._GetFlags(
            encryption=True, protection_scheme='cens', output_dash=True))
    self._CheckTestResults('encryption-cens', verify_decryption=True)

  def testEncryptionCbcs(self):
    self.assertPackageSuccess(
        self._GetStreams(['audio', 'video']),
        self._GetFlags(
            encryption=True, protection_scheme='cbcs', output_dash=True))
    self._CheckTestResults('encryption-cbcs', verify_decryption=True)

  def testEncryptionAndAdCues(self):
    self.assertPackageSuccess(
        self._GetStreams(['audio', 'video'], hls=True),
        self._GetFlags(encryption=True, output_dash=True, output_hls=True,
                       ad_cues='1.5'))
    self._CheckTestResults('encryption-and-ad-cues')

  def testEncryptionAndAdCuesSplitContent(self):
    self.assertPackageSuccess(
        self._GetStreams(
            ['audio', 'video'], hls=True, split_content_on_ad_cues=True),
        self._GetFlags(
            encryption=True, output_dash=True, output_hls=True, ad_cues='1.5'))
    self._CheckTestResults('encryption-and-ad-cues-split-content')

  def testHlsAudioVideoTextWithAdCues(self):
    streams = [
        self._GetStream('audio',
                        hls=True,
                        segmented=True),
        self._GetStream('video',
                        hls=True,
                        segmented=True),
        self._GetStream('text',
                        hls=True,
                        segmented=True,
                        test_file='bear-subtitle-english.vtt')
    ]
    flags = self._GetFlags(output_hls=True, ad_cues='1.5')
    self.assertPackageSuccess(streams, flags)
    self._CheckTestResults('hls-audio-video-text-with-ad-cues')

  def testVttTextToMp4WithAdCues(self):
    streams = [
        self._GetStream('audio',
                        hls=True,
                        segmented=True),
        self._GetStream('video',
                        hls=True,
                        segmented=True),
        self._GetStream('text',
                        hls=True,
                        segmented=True,
                        test_file='bear-subtitle-english.vtt',
                        output_format='mp4')
    ]
    flags = self._GetFlags(output_dash=True, output_hls=True,
                           generate_static_mpd=True, ad_cues='1.5')
    self.assertPackageSuccess(streams, flags)
    # Mpd cannot be validated right now since we don't generate determinstic
    # mpd with multiple inputs due to thread racing.
    # TODO(b/73349711): Generate determinstic mpd or at least validate mpd
    #                   schema.
    self._CheckTestResults(
        'vtt-text-to-mp4-with-ad-cues',
        diff_files_policy=DiffFilesPolicy(
            allowed_diff_files=['output.mpd'], exact=False))

  def testWebmSubsampleEncryption(self):
    streams = [
        self._GetStream('video',
                        output_format='webm',
                        test_file='bear-320x180-vp9-altref.webm')
    ]
    self.assertPackageSuccess(streams,
                              self._GetFlags(encryption=True, output_dash=True))
    self._CheckTestResults('webm-subsample-encryption', verify_decryption=True)

  def testWebmVp9FullSampleEncryption(self):
    streams = [
        self._GetStream('video',
                        output_format='webm',
                        test_file='bear-320x180-vp9-altref.webm')
    ]
    flags = self._GetFlags(
        encryption=True, vp9_subsample_encryption=False, output_dash=True)

    self.assertPackageSuccess(streams, flags)
    self._CheckTestResults(
        'webm-vp9-full-sample-encryption', verify_decryption=True)

  def testAvcTsWithEncryption(self):
    # Currently we only support live packaging for ts.
    self.assertPackageSuccess(
        self._GetStreams(
            ['audio', 'video'],
            output_format='ts',
            segmented=True,
            hls=True,
            test_files=['bear-640x360.ts']),
        self._GetFlags(encryption=True, output_hls=True))
    self._CheckTestResults('avc-ts-with-encryption')

  def testAvcTsAacPackedAudioWithEncryption(self):
    # Currently we only support live packaging for ts.
    streams = [
        self._GetStream(
            'audio',
            output_format='aac',
            segmented=True,
            hls=True,
            test_file='bear-640x360.ts'),
        self._GetStream(
            'video',
            output_format='ts',
            segmented=True,
            hls=True,
            test_file='bear-640x360.ts')
    ]
    flags = self._GetFlags(encryption=True, output_hls=True)

    self.assertPackageSuccess(streams, flags)
    self._CheckTestResults('avc-ts-aac-packed-audio-with-encryption')

  def testAvcTsWithEncryptionAndFairPlay(self):
    # Currently we only support live packaging for ts.
    self.assertPackageSuccess(
        self._GetStreams(
            ['audio', 'video'],
            output_format='ts',
            segmented=True,
            hls=True,
            test_files=['bear-640x360.ts']),
        self._GetFlags(encryption=True, output_hls=True, fairplay=True))
    self._CheckTestResults('avc-ts-with-encryption-and-fairplay')

  def testAvcAc3TsWithEncryption(self):
    # Currently we only support live packaging for ts.
    self.assertPackageSuccess(
        self._GetStreams(
            ['audio', 'video'],
            output_format='ts',
            segmented=True,
            hls=True,
            test_files=['bear-640x360-ac3.ts']),
        self._GetFlags(encryption=True, output_hls=True))
    self._CheckTestResults('avc-ac3-ts-with-encryption')

  def testAvcTsAc3PackedAudioWithEncryption(self):
    # Currently we only support live packaging for ts.
    streams = [
        self._GetStream(
            'audio',
            output_format='ac3',
            segmented=True,
            hls=True,
            test_file='bear-640x360-ac3.ts'),
        self._GetStream(
            'video',
            output_format='ts',
            segmented=True,
            hls=True,
            test_file='bear-640x360-ac3.ts')
    ]
    flags = self._GetFlags(encryption=True, output_hls=True)

    self.assertPackageSuccess(streams, flags)
    self._CheckTestResults('avc-ts-ac3-packed-audio-with-encryption')

  def testAvcTsWithEncryptionExerciseEmulationPrevention(self):
    self.encryption_key = 'ad7e9786def9159db6724be06dfcde7a'
    # Currently we only support live packaging for ts.
    self.assertPackageSuccess(
        self._GetStreams(
            ['video'],
            output_format='ts',
            segmented=True,
            hls=True,
            test_files=['sintel-1024x436.mp4']),
        self._GetFlags(
            encryption=True,
            output_hls=True))
    self._CheckTestResults(
        'avc-ts-with-encryption-exercise-emulation-prevention')

  def testWebmWithEncryption(self):
    streams = [
        self._GetStream('video',
                        output_format='webm',
                        test_file='bear-640x360.webm')
    ]
    flags = self._GetFlags(encryption=True, output_dash=True)

    self.assertPackageSuccess(streams, flags)
    self._CheckTestResults('webm-with-encryption', verify_decryption=True)

  def testHevcWithEncryption(self):
    streams = [
        self._GetStream('video', test_file='bear-640x360-hevc.mp4')
    ]
    flags = self._GetFlags(encryption=True, output_dash=True)

    self.assertPackageSuccess(streams, flags)
    self._CheckTestResults('hevc-with-encryption', verify_decryption=True)

  def testVp8Mp4WithEncryption(self):
    streams = [
        self._GetStream('video',
                        output_format='mp4',
                        test_file='bear-640x360.webm')
    ]
    flags = self._GetFlags(encryption=True, output_dash=True)

    self.assertPackageSuccess(streams, flags)
    self._CheckTestResults('vp8-mp4-with-encryption', verify_decryption=True)

  def testOpusVp9Mp4WithEncryption(self):
    streams = [
        self._GetStream('audio',
                        output_format='mp4',
                        test_file='bear-320x240-vp9-opus.webm'),
        self._GetStream('video',
                        output_format='mp4',
                        test_file='bear-320x240-vp9-opus.webm'),
    ]
    flags = self._GetFlags(encryption=True, output_dash=True)

    self.assertPackageSuccess(streams, flags)
    self._CheckTestResults(
        'opus-vp9-mp4-with-encryption', verify_decryption=True)

  def testFlacWithEncryption(self):
    streams = [
        self._GetStream(
            'audio', output_format='mp4', test_file='bear-flac.mp4'),
    ]
    flags = self._GetFlags(encryption=True, output_dash=True, output_hls=True)

    self.assertPackageSuccess(streams, flags)
    self._CheckTestResults('flac-with-encryption', verify_decryption=True)

  def testWvmInput(self):
    self.encryption_key = '9248d245390e0a49d483ba9b43fc69c3'
    self.assertPackageSuccess(
        self._GetStreams(
            ['0', '1', '2', '3'], test_files=['bear-multi-configs.wvm']),
        self._GetFlags(decryption=True, output_dash=True))
    # Output timescale is 90000.
    self._CheckTestResults('wvm-input')

  # TODO(kqyang): Fix shared_library not supporting strip_parameter_set_nalus
  # problem.
  @unittest.skipUnless(
      test_env.options.libpackager_type == 'static_library',
      'libpackager shared_library does not support '
      '--strip_parameter_set_nalus flag.'
  )
  def testWvmInputWithoutStrippingParameterSetNalus(self):
    self.encryption_key = '9248d245390e0a49d483ba9b43fc69c3'
    self.assertPackageSuccess(
        self._GetStreams(
            ['0', '1', '2', '3'], test_files=['bear-multi-configs.wvm']),
        self._GetFlags(
            strip_parameter_set_nalus=False, decryption=True, output_dash=True))
    # Output timescale is 90000.
    self._CheckTestResults('wvm-input-without-stripping-parameters-set-nalus')

  def testEncryptionAndRandomIv(self):
    self.assertPackageSuccess(
        self._GetStreams(['audio', 'video']),
        self._GetFlags(encryption=True, random_iv=True, output_dash=True))
    # The outputs are encrypted with random iv, so they are not the same as
    # golden files.
    self._CheckTestResults(
        'encryption',
        verify_decryption=True,
        diff_files_policy=DiffFilesPolicy(
            allowed_diff_files=[
                'bear-640x360-audio.mp4', 'bear-640x360-video.mp4'
            ],
            exact=True,
            allow_updating_golden_files=False))

  def testEncryptionAndRealClock(self):
    self.assertPackageSuccess(
        self._GetStreams(['audio', 'video']),
        self._GetFlags(encryption=True, output_dash=True, use_fake_clock=False))
    # The outputs are generated with real clock, so they are not the same as
    # golden files.
    self._CheckTestResults(
        'encryption',
        verify_decryption=True,
        diff_files_policy=DiffFilesPolicy(
            allowed_diff_files=[
                'bear-640x360-audio.mp4', 'bear-640x360-video.mp4'
            ],
            exact=True,
            allow_updating_golden_files=False))

  def testEncryptionAndNonDashIfIop(self):
    self.assertPackageSuccess(
        self._GetStreams(['audio', 'video']),
        self._GetFlags(encryption=True, dash_if_iop=False, output_dash=True))
    self._CheckTestResults('encryption-and-non-dash-if-iop')

  def testEncryptionAndOutputMediaInfo(self):
    self.assertPackageSuccess(
        self._GetStreams(['audio', 'video']),
        self._GetFlags(encryption=True, output_media_info=True))
    self._CheckTestResults('encryption-and-output-media-info')

  def testEncryptionAndOutputMediaInfoAndMpdFromMediaInfo(self):
    self.assertPackageSuccess(
        # The order is not determinstic if there are more than one
        # AdaptationSets, so only one is included here.
        self._GetStreams(['video']),
        self._GetFlags(encryption=True, output_media_info=True))
    self.assertMpdGeneratorSuccess()
    self._CheckTestResults(
        'encryption-and-output-media-info-and-mpd-from-media-info')

  def testHlsSingleSegmentMp4Encrypted(self):
    self.assertPackageSuccess(
        self._GetStreams(['audio', 'video'], hls=True),
        self._GetFlags(encryption=True, output_hls=True))
    self._CheckTestResults('hls-single-segment-mp4-encrypted')

  def testEc3AndHlsSingleSegmentMp4Encrypted(self):
    self.assertPackageSuccess(
        self._GetStreams(
            ['audio', 'video'], hls=True, test_files=['bear-640x360-ec3.mp4']),
        self._GetFlags(encryption=True, output_hls=True))
    self._CheckTestResults('ec3-and-hls-single-segment-mp4-encrypted')

  def testEc3PackedAudioEncrypted(self):
    streams = [
        self._GetStream(
            'audio',
            output_format='ec3',
            segmented=True,
            hls=True,
            test_file='bear-640x360-ec3.mp4'),
        self._GetStream(
            'video',
            output_format='ts',
            segmented=True,
            hls=True,
            test_file='bear-640x360-ec3.mp4')
    ]
    flags = self._GetFlags(encryption=True, output_hls=True)

    self.assertPackageSuccess(streams, flags)
    self._CheckTestResults('ec3-packed-audio-encrypted')

  # Test HLS with multi-segment mp4 and content in subdirectories.
  def testHlsMultiSegmentMp4WithCustomPath(self):
    test_file = os.path.join(self.test_data_dir, 'bear-640x360.mp4')
    # {tmp}/audio/audio-init.mp4, {tmp}/audio/audio-1.m4s etc.
    audio_output_prefix = os.path.join(self.tmp_dir, 'audio', 'audio')
    # {tmp}/video/video-init.mp4, {tmp}/video/video-1.m4s etc.
    video_output_prefix = os.path.join(self.tmp_dir, 'video', 'video')

    self.assertPackageSuccess(
        [
            'input=%s,stream=audio,init_segment=%s-init.mp4,'
            'segment_template=%s-$Number$.m4s,playlist_name=audio/audio.m3u8' %
            (test_file, audio_output_prefix, audio_output_prefix),
            'input=%s,stream=video,init_segment=%s-init.mp4,'
            'segment_template=%s-$Number$.m4s,playlist_name=video/video.m3u8' %
            (test_file, video_output_prefix, video_output_prefix),
        ],
        self._GetFlags(output_hls=True))

    self._CheckTestResults('hls-multi-segment-mp4-with-custom-path')

  def testLiveProfile(self):
    self.assertPackageSuccess(
        self._GetStreams(['audio', 'video'], segmented=True),
        self._GetFlags(
            output_dash=True,
            utc_timings='urn:mpeg:dash:utc:http-xsdate:2014='
            'http://foo.bar/my_body_is_the_current_date_and_time,'
            'urn:mpeg:dash:utc:http-head:2014='
            'http://foo.bar/check_me_for_the_date_header'))
    self._CheckTestResults('live-profile')

  def testLiveProfileWithWebM(self):
    streams = self._GetStreams(
        ['audio', 'video'],
        segmented=True,
        output_format='webm',
        test_file='bear-640x360.webm'
    )
    flags = self._GetFlags(output_dash=True, output_hls=True)

    self.assertPackageSuccess(streams, flags)
    self._CheckTestResults('live-profile-with-webm')

  def testLiveStaticProfile(self):
    self.assertPackageSuccess(
        self._GetStreams(['audio', 'video'], segmented=True),
        self._GetFlags(output_dash=True, generate_static_mpd=True))
    self._CheckTestResults('live-static-profile')

  def testLiveProfileAndEncryption(self):
    self.assertPackageSuccess(
        self._GetStreams(['audio', 'video'], segmented=True),
        self._GetFlags(encryption=True, output_dash=True))
    self._CheckTestResults('live-profile-and-encryption')

  def testLiveProfileAndEncryptionAndNonDashIfIop(self):
    self.assertPackageSuccess(
        self._GetStreams(['audio', 'video'], segmented=True),
        self._GetFlags(encryption=True, dash_if_iop=False, output_dash=True))
    self._CheckTestResults(
        'live-profile-and-encryption-and-non-dash-if-iop')

  def testLiveProfileAndEncryptionAndMultFiles(self):
    self.assertPackageSuccess(
        self._GetStreams(['audio', 'video'],
                         segmented=True,
                         test_files=['bear-1280x720.mp4', 'bear-640x360.mp4',
                                     'bear-320x180.mp4']),
        self._GetFlags(encryption=True, output_dash=True))
    # Mpd cannot be validated right now since we don't generate determinstic
    # mpd with multiple inputs due to thread racing.
    # TODO(b/73349711): Generate determinstic mpd or at least validate mpd
    #                   schema.
    self._CheckTestResults(
        'live-profile-and-encryption-and-mult-files',
        diff_files_policy=DiffFilesPolicy(
            allowed_diff_files=['output.mpd'], exact=False))

  def testLiveProfileAndKeyRotation(self):
    self.assertPackageSuccess(
        self._GetStreams(['audio', 'video'], segmented=True),
        self._GetFlags(encryption=True, key_rotation=True, output_dash=True))
    self._CheckTestResults('live-profile-and-key-rotation')

  def testLiveProfileAndKeyRotationCbcs(self):
    self.assertPackageSuccess(
        self._GetStreams(['audio', 'video'], segmented=True),
        self._GetFlags(
            encryption=True,
            protection_scheme='cbcs',
            key_rotation=True,
            output_dash=True))
    self._CheckTestResults('live-profile-and-key-rotation-cbcs')

  def testLiveProfileAndKeyRotationAndNoPsshInStream(self):
    self.assertPackageSuccess(
        self._GetStreams(['audio', 'video'], segmented=True),
        self._GetFlags(
            encryption=True,
            key_rotation=True,
            include_pssh_in_stream=False,
            output_dash=True))
    self._CheckTestResults(
        'live-profile-and-key-rotation-and-no-pssh-in-stream')

  def testLiveProfileAndKeyRotationAndNonDashIfIop(self):
    self.assertPackageSuccess(
        self._GetStreams(['audio', 'video'], segmented=True),
        self._GetFlags(
            encryption=True,
            key_rotation=True,
            dash_if_iop=False,
            output_dash=True))
    self._CheckTestResults(
        'live-profile-and-key-rotation-and-non-dash-if-iop')

  @unittest.skipUnless(test_env.has_aes_flags, 'Requires AES credentials.')
  def testWidevineEncryptionWithAes(self):
    flags = self._GetFlags(widevine_encryption=True, output_dash=True)
    flags += [
        '--signer=widevine_test',
        '--aes_signing_key=' + test_env.options.aes_signing_key,
        '--aes_signing_iv=' + test_env.options.aes_signing_iv
    ]
    self.assertPackageSuccess(self._GetStreams(['audio', 'video']), flags)
    self._AssertStreamInfo(self.output[0], 'is_encrypted: true')
    self._AssertStreamInfo(self.output[1], 'is_encrypted: true')

  @unittest.skipUnless(test_env.has_aes_flags, 'Requires AES credentials.')
  def testWidevineEncryptionWithAesAndMultFiles(self):
    flags = self._GetFlags(widevine_encryption=True, output_dash=True)
    flags += [
        '--signer=widevine_test',
        '--aes_signing_key=' + test_env.options.aes_signing_key,
        '--aes_signing_iv=' + test_env.options.aes_signing_iv
    ]
    self.assertPackageSuccess(
        self._GetStreams(['audio', 'video'],
                         test_files=['bear-1280x720.mp4', 'bear-640x360.mp4',
                                     'bear-320x180.mp4']), flags)
    with open(self.mpd_output, 'rb') as f:
      print f.read()
      # TODO(kqyang): Add some validations.

  @unittest.skipUnless(test_env.has_aes_flags, 'Requires AES credentials.')
  def testKeyRotationWithAes(self):
    flags = self._GetFlags(
        widevine_encryption=True, key_rotation=True, output_dash=True)
    flags += [
        '--signer=widevine_test',
        '--aes_signing_key=' + test_env.options.aes_signing_key,
        '--aes_signing_iv=' + test_env.options.aes_signing_iv
    ]
    self.assertPackageSuccess(self._GetStreams(['audio', 'video']), flags)
    self._AssertStreamInfo(self.output[0], 'is_encrypted: true')
    self._AssertStreamInfo(self.output[1], 'is_encrypted: true')

  @unittest.skipUnless(test_env.has_rsa_flags, 'Requires RSA credentials.')
  def testWidevineEncryptionWithRsa(self):
    flags = self._GetFlags(widevine_encryption=True, output_dash=True)
    flags += [
        '--signer=widevine_test',
        '--rsa_signing_key_path=' + test_env.options.rsa_signing_key_path
    ]
    self.assertPackageSuccess(self._GetStreams(['audio', 'video']), flags)
    self._AssertStreamInfo(self.output[0], 'is_encrypted: true')
    self._AssertStreamInfo(self.output[1], 'is_encrypted: true')

  def testHlsSegmentedWebVtt(self):
    streams = self._GetStreams(
        ['audio', 'video'], output_format='ts', segmented=True)
    streams += self._GetStreams(
        ['text'], test_files=['bear-subtitle-english.vtt'], segmented=True)

    flags = self._GetFlags(output_hls=True)

    self.assertPackageSuccess(streams, flags)
    self._CheckTestResults('hls-segmented-webvtt')

  def testBandwidthOverride(self):
    streams = [
        self._GetStream('audio', hls=True, bandwidth=11111),
        self._GetStream('video', hls=True, bandwidth=44444)
    ]

    flags = self._GetFlags(output_dash=True, output_hls=True)

    self.assertPackageSuccess(streams, flags)
    self._CheckTestResults('bandwidth-override')


class PackagerCommandParsingTest(PackagerAppTest):

  def testEncryptionWithIncorrectKeyIdLength1(self):
    self.encryption_key_id = self.encryption_key_id[0:-2]
    packaging_result = self.packager.Package(
        self._GetStreams(['video']), self._GetFlags(encryption=True))
    self.assertEqual(packaging_result, 1)

  def testEncryptionWithIncorrectKeyIdLength2(self):
    self.encryption_key_id += '12'
    packaging_result = self.packager.Package(
        self._GetStreams(['video']), self._GetFlags(encryption=True))
    self.assertEqual(packaging_result, 1)

  def testEncryptionWithInvalidKeyIdValue(self):
    self.encryption_key_id = self.encryption_key_id[0:-1] + 'g'
    packaging_result = self.packager.Package(
        self._GetStreams(['video']), self._GetFlags(encryption=True))
    self.assertEqual(packaging_result, 1)

  def testEncryptionWithIncorrectKeyLength1(self):
    self.encryption_key = self.encryption_key[0:-2]
    packaging_result = self.packager.Package(
        self._GetStreams(['video']), self._GetFlags(encryption=True))
    self.assertEqual(packaging_result, 1)

  def testEncryptionWithIncorrectKeyLength2(self):
    self.encryption_key += '12'
    packaging_result = self.packager.Package(
        self._GetStreams(['video']), self._GetFlags(encryption=True))
    self.assertEqual(packaging_result, 1)

  def testEncryptionWithInvalidKeyValue(self):
    self.encryption_key = self.encryption_key[0:-1] + 'g'
    packaging_result = self.packager.Package(
        self._GetStreams(['video']), self._GetFlags(encryption=True))
    self.assertEqual(packaging_result, 1)

  def testEncryptionWithIncorrectIvLength1(self):
    self.encryption_iv = self.encryption_iv[0:-2]
    packaging_result = self.packager.Package(
        self._GetStreams(['video']), self._GetFlags(encryption=True))
    self.assertEqual(packaging_result, 1)

  def testEncryptionWithIncorrectIvLength2(self):
    self.encryption_iv += '12'
    packaging_result = self.packager.Package(
        self._GetStreams(['video']), self._GetFlags(encryption=True))
    self.assertEqual(packaging_result, 1)

  def testEncryptionWithInvalidIvValue(self):
    self.encryption_iv = self.encryption_iv[0:-1] + 'g'
    packaging_result = self.packager.Package(
        self._GetStreams(['video']), self._GetFlags(encryption=True))
    self.assertEqual(packaging_result, 1)

  def testEncryptionWithInvalidPsshValue1(self):
    packaging_result = self.packager.Package(
        self._GetStreams(['video']),
        self._GetFlags(encryption=True) + ['--pssh=ag'])
    self.assertEqual(packaging_result, 1)

  def testEncryptionWithInvalidPsshValue2(self):
    packaging_result = self.packager.Package(
        self._GetStreams(['video']),
        self._GetFlags(encryption=True) + ['--pssh=1122'])
    self.assertEqual(packaging_result, 1)

  def testWidevineEncryptionInvalidContentId(self):
    self.widevine_content_id += 'ag'
    flags = self._GetFlags(widevine_encryption=True)
    flags += [
        '--signer=widevine_test', '--aes_signing_key=1122',
        '--aes_signing_iv=3344'
    ]
    packaging_result = self.packager.Package(
        self._GetStreams(['audio', 'video']), flags)
    self.assertEqual(packaging_result, 1)

  def testWidevineEncryptionInvalidAesSigningKey(self):
    flags = self._GetFlags(widevine_encryption=True)
    flags += [
        '--signer=widevine_test', '--aes_signing_key=11ag',
        '--aes_signing_iv=3344'
    ]
    packaging_result = self.packager.Package(
        self._GetStreams(['audio', 'video']), flags)
    self.assertEqual(packaging_result, 1)

  def testWidevineEncryptionInvalidAesSigningIv(self):
    flags = self._GetFlags(widevine_encryption=True)
    flags += [
        '--signer=widevine_test', '--aes_signing_key=1122',
        '--aes_signing_iv=33ag'
    ]
    packaging_result = self.packager.Package(
        self._GetStreams(['audio', 'video']), flags)
    self.assertEqual(packaging_result, 1)

  def testWidevineEncryptionMissingAesSigningKey(self):
    flags = self._GetFlags(widevine_encryption=True)
    flags += ['--signer=widevine_test', '--aes_signing_iv=3344']
    packaging_result = self.packager.Package(
        self._GetStreams(['audio', 'video']), flags)
    self.assertEqual(packaging_result, 1)

  def testWidevineEncryptionMissingAesSigningIv(self):
    flags = self._GetFlags(widevine_encryption=True)
    flags += ['--signer=widevine_test', '--aes_signing_key=1122']
    packaging_result = self.packager.Package(
        self._GetStreams(['audio', 'video']), flags)
    self.assertEqual(packaging_result, 1)

  def testWidevineEncryptionMissingSigner1(self):
    flags = self._GetFlags(widevine_encryption=True)
    flags += ['--aes_signing_key=1122', '--aes_signing_iv=3344']
    packaging_result = self.packager.Package(
        self._GetStreams(['audio', 'video']), flags)
    self.assertEqual(packaging_result, 1)

  def testWidevineEncryptionMissingSigner2(self):
    flags = self._GetFlags(widevine_encryption=True)
    flags += ['--rsa_signing_key_path=/tmp/test']
    packaging_result = self.packager.Package(
        self._GetStreams(['audio', 'video']), flags)
    self.assertEqual(packaging_result, 1)

  def testWidevineEncryptionSignerOnly(self):
    flags = self._GetFlags(widevine_encryption=True)
    flags += ['--signer=widevine_test']
    packaging_result = self.packager.Package(
        self._GetStreams(['audio', 'video']), flags)
    self.assertEqual(packaging_result, 1)

  def testWidevineEncryptionAesSigningAndRsaSigning(self):
    flags = self._GetFlags(widevine_encryption=True)
    flags += [
        '--signer=widevine_test',
        '--aes_signing_key=1122',
        '--aes_signing_iv=3344',
        '--rsa_signing_key_path=/tmp/test',
    ]
    packaging_result = self.packager.Package(
        self._GetStreams(['audio', 'video']), flags)
    self.assertEqual(packaging_result, 1)

  def testAudioVideoWithNotExistText(self):
    audio_video_stream = self._GetStreams(['audio', 'video'])
    text_stream = self._GetStreams(['text'], test_files=['not-exist.vtt'])
    packaging_result = self.packager.Package(audio_video_stream + text_stream,
                                             self._GetFlags())
    # Expect the test to fail but we do not expect a crash.
    self.assertEqual(packaging_result, 1)

  def testInconsistentOutputAndSegmentTemplateFormat(self):
    test_file = os.path.join(self.test_data_dir, 'bear-640x360.mp4')
    video_output_prefix = os.path.join(self.tmp_dir, 'video')

    packaging_result = self.packager.Package([
        'input=%s,stream=video,init_segment=%s-init.mp4,'
        'segment_template=%s-$Number$.webm' %
        (test_file, video_output_prefix, video_output_prefix),
    ], self._GetFlags())
    # Expect the test to fail but we do not expect a crash.
    self.assertEqual(packaging_result, 1)


if __name__ == '__main__':
  unittest.main()
