// Copyright 2017 Google Inc. All rights reserved.
//
// Use of this source code is governed by a BSD-style
// license that can be found in the LICENSE file or at
// https://developers.google.com/open-source/licenses/bsd
//
// Defines command line flags for protection systems.

#include "packager/app/protection_system_flags.h"

DEFINE_string(additional_protection_systems,
              "",
              "Generate additional protection systems in addition to the "
              "native protection system provided by the key source. Supported "
              "protection systems include Widevine, PlayReady, FairPlay, and "
              "CommonSystem (https://goo.gl/s8RIhr).");
