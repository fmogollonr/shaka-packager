// Copyright 2017 Google Inc. All rights reserved.
//
// Use of this source code is governed by a BSD-style
// license that can be found in the LICENSE file or at
// https://developers.google.com/open-source/licenses/bsd
//
// Defines cuepoint generator flags.

#include "packager/app/subtitle_flags.h"

DEFINE_bool(live_subtitles,
              false,
              "If enabled, forces packager to continuously scan for subtitles."
              "This option is intended for live streaming with subtitles. ");
