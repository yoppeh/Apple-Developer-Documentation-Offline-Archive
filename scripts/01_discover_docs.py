#!/usr/bin/env python3
"""
Apple Documentation Discovery Crawler
Recursively discovers all documentation pages through Apple's JSON API
"""

import asyncio
import aiohttp
import hashlib
import json
import time
from datetime import datetime
from pathlib import Path
from typing import Set, Dict, List
from tqdm import tqdm
import argparse

from apple_http import AdaptiveRateLimiter, fetch


# Framework root URLs
FRAMEWORK_ROOTS = {
    'appintents': 'https://developer.apple.com/tutorials/data/documentation/appintents.json',
    'appkit': 'https://developer.apple.com/tutorials/data/documentation/appkit.json',
    'arkit': 'https://developer.apple.com/tutorials/data/documentation/arkit.json',
    'avfoundation': 'https://developer.apple.com/tutorials/data/documentation/avfoundation.json',
    'charts': 'https://developer.apple.com/tutorials/data/documentation/charts.json',
    'cloudkit': 'https://developer.apple.com/tutorials/data/documentation/cloudkit.json',
    'combine': 'https://developer.apple.com/tutorials/data/documentation/combine.json',
    'contacts': 'https://developer.apple.com/tutorials/data/documentation/contacts.json',
    'coredata': 'https://developer.apple.com/tutorials/data/documentation/coredata.json',
    'coregraphics': 'https://developer.apple.com/tutorials/data/documentation/coregraphics.json',
    'coreimage': 'https://developer.apple.com/tutorials/data/documentation/coreimage.json',
    'coreml': 'https://developer.apple.com/tutorials/data/documentation/coreml.json',
    'corespotlight': 'https://developer.apple.com/tutorials/data/documentation/corespotlight.json',
    'cryptokit': 'https://developer.apple.com/tutorials/data/documentation/cryptokit.json',
    'eventkit': 'https://developer.apple.com/tutorials/data/documentation/eventkit.json',
    'foundation': 'https://developer.apple.com/tutorials/data/documentation/foundation.json',
    'foundationmodels': 'https://developer.apple.com/tutorials/data/documentation/foundationmodels.json',
    'kernel': 'https://developer.apple.com/tutorials/data/documentation/kernel.json',
    'mapkit': 'https://developer.apple.com/tutorials/data/documentation/mapkit.json',
    'messageui': 'https://developer.apple.com/tutorials/data/documentation/messageui.json',
    'multipeerconnectivity': 'https://developer.apple.com/tutorials/data/documentation/multipeerconnectivity.json',
    'naturallanguage': 'https://developer.apple.com/tutorials/data/documentation/naturallanguage.json',
    'pdfkit': 'https://developer.apple.com/tutorials/data/documentation/pdfkit.json',
    'photosui': 'https://developer.apple.com/tutorials/data/documentation/photosui.json',
    'quartzcore': 'https://developer.apple.com/tutorials/data/documentation/quartzcore.json',
    'realitykit': 'https://developer.apple.com/tutorials/data/documentation/realitykit.json',
    'security': 'https://developer.apple.com/tutorials/data/documentation/security.json',
    'speech': 'https://developer.apple.com/tutorials/data/documentation/speech.json',
    'spritekit': 'https://developer.apple.com/tutorials/data/documentation/spritekit.json',
    'swift': 'https://developer.apple.com/tutorials/data/documentation/swift.json',
    'swiftdata': 'https://developer.apple.com/tutorials/data/documentation/swiftdata.json',
    'swiftui': 'https://developer.apple.com/tutorials/data/documentation/swiftui.json',
    'uikit': 'https://developer.apple.com/tutorials/data/documentation/uikit.json',
    'uniformtypeidentifiers': 'https://developer.apple.com/tutorials/data/documentation/uniformtypeidentifiers.json',
    'usernotifications': 'https://developer.apple.com/tutorials/data/documentation/usernotifications.json',
    'visionkit': 'https://developer.apple.com/tutorials/data/documentation/visionkit.json',
    'webkit': 'https://developer.apple.com/tutorials/data/documentation/webkit.json',
    'xcode': 'https://developer.apple.com/tutorials/data/documentation/xcode.json',
    # Remaining technologies from documentation/technologies.json
    'accelerate': 'https://developer.apple.com/tutorials/data/documentation/accelerate.json',
    'accessibility': 'https://developer.apple.com/tutorials/data/documentation/accessibility.json',
    'accessoryaccess': 'https://developer.apple.com/tutorials/data/documentation/accessoryaccess.json',
    'accessoryliveactivities': 'https://developer.apple.com/tutorials/data/documentation/accessoryliveactivities.json',
    'accessorynotifications': 'https://developer.apple.com/tutorials/data/documentation/accessorynotifications.json',
    'accessorysetupkit': 'https://developer.apple.com/tutorials/data/documentation/accessorysetupkit.json',
    'accessorytransportextension': 'https://developer.apple.com/tutorials/data/documentation/accessorytransportextension.json',
    'accountdatatransfer': 'https://developer.apple.com/tutorials/data/documentation/accountdatatransfer.json',
    'accountorganizationaldatasharing': 'https://developer.apple.com/tutorials/data/documentation/accountorganizationaldatasharing.json',
    'accounts': 'https://developer.apple.com/tutorials/data/documentation/accounts.json',
    'activitykit': 'https://developer.apple.com/tutorials/data/documentation/activitykit.json',
    'adattributionkit': 'https://developer.apple.com/tutorials/data/documentation/adattributionkit.json',
    'addressbook': 'https://developer.apple.com/tutorials/data/documentation/addressbook.json',
    'addressbookui': 'https://developer.apple.com/tutorials/data/documentation/addressbookui.json',
    'adservices': 'https://developer.apple.com/tutorials/data/documentation/adservices.json',
    'adsupport': 'https://developer.apple.com/tutorials/data/documentation/adsupport.json',
    'advancedcommerceapi': 'https://developer.apple.com/tutorials/data/documentation/advancedcommerceapi.json',
    'alarmkit': 'https://developer.apple.com/tutorials/data/documentation/alarmkit.json',
    'analytics-reports': 'https://developer.apple.com/tutorials/data/documentation/analytics-reports.json',
    'appclip': 'https://developer.apple.com/tutorials/data/documentation/appclip.json',
    'appdatatransfer': 'https://developer.apple.com/tutorials/data/documentation/appdatatransfer.json',
    'appintentstesting': 'https://developer.apple.com/tutorials/data/documentation/appintentstesting.json',
    'apple-ads-platform-api': 'https://developer.apple.com/tutorials/data/documentation/apple-ads-platform-api.json',
    'apple-school-and-business-manager-api': 'https://developer.apple.com/tutorials/data/documentation/apple-school-and-business-manager-api.json',
    'apple-silicon': 'https://developer.apple.com/tutorials/data/documentation/apple-silicon.json',
    'apple_ads': 'https://developer.apple.com/tutorials/data/documentation/apple_ads.json',
    'applearchive': 'https://developer.apple.com/tutorials/data/documentation/applearchive.json',
    'applemapsserverapi': 'https://developer.apple.com/tutorials/data/documentation/applemapsserverapi.json',
    'applemusicapi': 'https://developer.apple.com/tutorials/data/documentation/applemusicapi.json',
    'applemusicfeed': 'https://developer.apple.com/tutorials/data/documentation/applemusicfeed.json',
    'applenews': 'https://developer.apple.com/tutorials/data/documentation/applenews.json',
    'applepaymerchanttokenusageinformation': 'https://developer.apple.com/tutorials/data/documentation/applepaymerchanttokenusageinformation.json',
    'applepayontheweb': 'https://developer.apple.com/tutorials/data/documentation/applepayontheweb.json',
    'applepaywebmerchantregistrationapi': 'https://developer.apple.com/tutorials/data/documentation/applepaywebmerchantregistrationapi.json',
    'applepencil': 'https://developer.apple.com/tutorials/data/documentation/applepencil.json',
    'appletvfeed': 'https://developer.apple.com/tutorials/data/documentation/appletvfeed.json',
    'applicationservices': 'https://developer.apple.com/tutorials/data/documentation/applicationservices.json',
    'applicensedeliverysdk': 'https://developer.apple.com/tutorials/data/documentation/applicensedeliverysdk.json',
    'appmigrationkit': 'https://developer.apple.com/tutorials/data/documentation/appmigrationkit.json',
    'appstoreconnectapi': 'https://developer.apple.com/tutorials/data/documentation/appstoreconnectapi.json',
    'appstorereceipts': 'https://developer.apple.com/tutorials/data/documentation/appstorereceipts.json',
    'appstoreserverapi': 'https://developer.apple.com/tutorials/data/documentation/appstoreserverapi.json',
    'appstoreservernotifications': 'https://developer.apple.com/tutorials/data/documentation/appstoreservernotifications.json',
    'apptrackingtransparency': 'https://developer.apple.com/tutorials/data/documentation/apptrackingtransparency.json',
    'assetslibrary': 'https://developer.apple.com/tutorials/data/documentation/assetslibrary.json',
    'assignables': 'https://developer.apple.com/tutorials/data/documentation/assignables.json',
    'audioaccessorykit': 'https://developer.apple.com/tutorials/data/documentation/audioaccessorykit.json',
    'audiodriverkit': 'https://developer.apple.com/tutorials/data/documentation/audiodriverkit.json',
    'audiotoolbox': 'https://developer.apple.com/tutorials/data/documentation/audiotoolbox.json',
    'audiounit': 'https://developer.apple.com/tutorials/data/documentation/audiounit.json',
    'authenticationservices': 'https://developer.apple.com/tutorials/data/documentation/authenticationservices.json',
    'automateddeviceenrollment': 'https://developer.apple.com/tutorials/data/documentation/automateddeviceenrollment.json',
    'automaticassessmentconfiguration': 'https://developer.apple.com/tutorials/data/documentation/automaticassessmentconfiguration.json',
    'automaticsigninapi': 'https://developer.apple.com/tutorials/data/documentation/automaticsigninapi.json',
    'automator': 'https://developer.apple.com/tutorials/data/documentation/automator.json',
    'avfaudio': 'https://developer.apple.com/tutorials/data/documentation/avfaudio.json',
    'avkit': 'https://developer.apple.com/tutorials/data/documentation/avkit.json',
    'avrouting': 'https://developer.apple.com/tutorials/data/documentation/avrouting.json',
    'avsystemrouting': 'https://developer.apple.com/tutorials/data/documentation/avsystemrouting.json',
    'backgroundassets': 'https://developer.apple.com/tutorials/data/documentation/backgroundassets.json',
    'backgroundtasks': 'https://developer.apple.com/tutorials/data/documentation/backgroundtasks.json',
    'blockstoragedevicedriverkit': 'https://developer.apple.com/tutorials/data/documentation/blockstoragedevicedriverkit.json',
    'browserenginecore': 'https://developer.apple.com/tutorials/data/documentation/browserenginecore.json',
    'browserenginekit': 'https://developer.apple.com/tutorials/data/documentation/browserenginekit.json',
    'browserkit': 'https://developer.apple.com/tutorials/data/documentation/browserkit.json',
    'bundleresources': 'https://developer.apple.com/tutorials/data/documentation/bundleresources.json',
    'callkit': 'https://developer.apple.com/tutorials/data/documentation/callkit.json',
    'carkey': 'https://developer.apple.com/tutorials/data/documentation/carkey.json',
    'carplay': 'https://developer.apple.com/tutorials/data/documentation/carplay.json',
    'cfnetwork': 'https://developer.apple.com/tutorials/data/documentation/cfnetwork.json',
    'cinematic': 'https://developer.apple.com/tutorials/data/documentation/cinematic.json',
    'cktooljs': 'https://developer.apple.com/tutorials/data/documentation/cktooljs.json',
    'classkit': 'https://developer.apple.com/tutorials/data/documentation/classkit.json',
    'classkitcatalogapi': 'https://developer.apple.com/tutorials/data/documentation/classkitcatalogapi.json',
    'classkitui': 'https://developer.apple.com/tutorials/data/documentation/classkitui.json',
    'clockkit': 'https://developer.apple.com/tutorials/data/documentation/clockkit.json',
    'cloudkitjs': 'https://developer.apple.com/tutorials/data/documentation/cloudkitjs.json',
    'collaboration': 'https://developer.apple.com/tutorials/data/documentation/collaboration.json',
    'colorsync': 'https://developer.apple.com/tutorials/data/documentation/colorsync.json',
    'compositorservices': 'https://developer.apple.com/tutorials/data/documentation/compositorservices.json',
    'compression': 'https://developer.apple.com/tutorials/data/documentation/compression.json',
    'computegraph': 'https://developer.apple.com/tutorials/data/documentation/computegraph.json',
    'contactprovider': 'https://developer.apple.com/tutorials/data/documentation/contactprovider.json',
    'contactsui': 'https://developer.apple.com/tutorials/data/documentation/contactsui.json',
    'coreai': 'https://developer.apple.com/tutorials/data/documentation/coreai.json',
    'coreaudio': 'https://developer.apple.com/tutorials/data/documentation/coreaudio.json',
    'coreaudiokit': 'https://developer.apple.com/tutorials/data/documentation/coreaudiokit.json',
    'coreaudiotypes': 'https://developer.apple.com/tutorials/data/documentation/coreaudiotypes.json',
    'corebluetooth': 'https://developer.apple.com/tutorials/data/documentation/corebluetooth.json',
    'corefoundation': 'https://developer.apple.com/tutorials/data/documentation/corefoundation.json',
    'corehaptics': 'https://developer.apple.com/tutorials/data/documentation/corehaptics.json',
    'corehid': 'https://developer.apple.com/tutorials/data/documentation/corehid.json',
    'corelocation': 'https://developer.apple.com/tutorials/data/documentation/corelocation.json',
    'corelocationui': 'https://developer.apple.com/tutorials/data/documentation/corelocationui.json',
    'coremedia': 'https://developer.apple.com/tutorials/data/documentation/coremedia.json',
    'coremediaio': 'https://developer.apple.com/tutorials/data/documentation/coremediaio.json',
    'coremidi': 'https://developer.apple.com/tutorials/data/documentation/coremidi.json',
    'coremotion': 'https://developer.apple.com/tutorials/data/documentation/coremotion.json',
    'corenfc': 'https://developer.apple.com/tutorials/data/documentation/corenfc.json',
    'coreservices': 'https://developer.apple.com/tutorials/data/documentation/coreservices.json',
    'coretelephony': 'https://developer.apple.com/tutorials/data/documentation/coretelephony.json',
    'coretext': 'https://developer.apple.com/tutorials/data/documentation/coretext.json',
    'coretransferable': 'https://developer.apple.com/tutorials/data/documentation/coretransferable.json',
    'corevideo': 'https://developer.apple.com/tutorials/data/documentation/corevideo.json',
    'corewlan': 'https://developer.apple.com/tutorials/data/documentation/corewlan.json',
    'crashreportextension': 'https://developer.apple.com/tutorials/data/documentation/crashreportextension.json',
    'createml': 'https://developer.apple.com/tutorials/data/documentation/createml.json',
    'createmlcomponents': 'https://developer.apple.com/tutorials/data/documentation/createmlcomponents.json',
    'cryptotokenkit': 'https://developer.apple.com/tutorials/data/documentation/cryptotokenkit.json',
    'darwinnotify': 'https://developer.apple.com/tutorials/data/documentation/darwinnotify.json',
    'datadetection': 'https://developer.apple.com/tutorials/data/documentation/datadetection.json',
    'declaredagerange': 'https://developer.apple.com/tutorials/data/documentation/declaredagerange.json',
    'developertoolssupport': 'https://developer.apple.com/tutorials/data/documentation/developertoolssupport.json',
    'deviceactivity': 'https://developer.apple.com/tutorials/data/documentation/deviceactivity.json',
    'devicecheck': 'https://developer.apple.com/tutorials/data/documentation/devicecheck.json',
    'devicediscoveryextension': 'https://developer.apple.com/tutorials/data/documentation/devicediscoveryextension.json',
    'devicediscoveryui': 'https://developer.apple.com/tutorials/data/documentation/devicediscoveryui.json',
    'devicemanagement': 'https://developer.apple.com/tutorials/data/documentation/devicemanagement.json',
    'diskarbitration': 'https://developer.apple.com/tutorials/data/documentation/diskarbitration.json',
    'diskimagekit': 'https://developer.apple.com/tutorials/data/documentation/diskimagekit.json',
    'dispatch': 'https://developer.apple.com/tutorials/data/documentation/dispatch.json',
    'distributed': 'https://developer.apple.com/tutorials/data/documentation/distributed.json',
    'dnssd': 'https://developer.apple.com/tutorials/data/documentation/dnssd.json',
    'dockkit': 'https://developer.apple.com/tutorials/data/documentation/dockkit.json',
    'driverkit': 'https://developer.apple.com/tutorials/data/documentation/driverkit.json',
    'endpointsecurity': 'https://developer.apple.com/tutorials/data/documentation/endpointsecurity.json',
    'energykit': 'https://developer.apple.com/tutorials/data/documentation/energykit.json',
    'enterpriseprogramapi': 'https://developer.apple.com/tutorials/data/documentation/enterpriseprogramapi.json',
    'evaluations': 'https://developer.apple.com/tutorials/data/documentation/evaluations.json',
    'eventkitui': 'https://developer.apple.com/tutorials/data/documentation/eventkitui.json',
    'exceptionhandling': 'https://developer.apple.com/tutorials/data/documentation/exceptionhandling.json',
    'executionpolicy': 'https://developer.apple.com/tutorials/data/documentation/executionpolicy.json',
    'exposurenotification': 'https://developer.apple.com/tutorials/data/documentation/exposurenotification.json',
    'extensionfoundation': 'https://developer.apple.com/tutorials/data/documentation/extensionfoundation.json',
    'extensionkit': 'https://developer.apple.com/tutorials/data/documentation/extensionkit.json',
    'externalaccessory': 'https://developer.apple.com/tutorials/data/documentation/externalaccessory.json',
    'externalpurchaseserverapi': 'https://developer.apple.com/tutorials/data/documentation/externalpurchaseserverapi.json',
    'familycontrols': 'https://developer.apple.com/tutorials/data/documentation/familycontrols.json',
    'fileprovider': 'https://developer.apple.com/tutorials/data/documentation/fileprovider.json',
    'fileproviderui': 'https://developer.apple.com/tutorials/data/documentation/fileproviderui.json',
    'financekit': 'https://developer.apple.com/tutorials/data/documentation/financekit.json',
    'financekitui': 'https://developer.apple.com/tutorials/data/documentation/financekitui.json',
    'findersync': 'https://developer.apple.com/tutorials/data/documentation/findersync.json',
    'forcefeedback': 'https://developer.apple.com/tutorials/data/documentation/forcefeedback.json',
    'foveatedstreaming': 'https://developer.apple.com/tutorials/data/documentation/foveatedstreaming.json',
    'fskit': 'https://developer.apple.com/tutorials/data/documentation/fskit.json',
    'gamecontroller': 'https://developer.apple.com/tutorials/data/documentation/gamecontroller.json',
    'gamekit': 'https://developer.apple.com/tutorials/data/documentation/gamekit.json',
    'gameplaykit': 'https://developer.apple.com/tutorials/data/documentation/gameplaykit.json',
    'gamesave': 'https://developer.apple.com/tutorials/data/documentation/gamesave.json',
    'geotoolbox': 'https://developer.apple.com/tutorials/data/documentation/geotoolbox.json',
    'glkit': 'https://developer.apple.com/tutorials/data/documentation/glkit.json',
    'groupactivities': 'https://developer.apple.com/tutorials/data/documentation/groupactivities.json',
    'gss': 'https://developer.apple.com/tutorials/data/documentation/gss.json',
    'healthkit': 'https://developer.apple.com/tutorials/data/documentation/healthkit.json',
    'hiddriverkit': 'https://developer.apple.com/tutorials/data/documentation/hiddriverkit.json',
    'homeextensions': 'https://developer.apple.com/tutorials/data/documentation/homeextensions.json',
    'homekit': 'https://developer.apple.com/tutorials/data/documentation/homekit.json',
    'http-live-streaming': 'https://developer.apple.com/tutorials/data/documentation/http-live-streaming.json',
    'hvf': 'https://developer.apple.com/tutorials/data/documentation/hvf.json',
    'hypervisor': 'https://developer.apple.com/tutorials/data/documentation/hypervisor.json',
    'iad': 'https://developer.apple.com/tutorials/data/documentation/iad.json',
    'identitydocumentservices': 'https://developer.apple.com/tutorials/data/documentation/identitydocumentservices.json',
    'identitydocumentservicesui': 'https://developer.apple.com/tutorials/data/documentation/identitydocumentservicesui.json',
    'identitylookup': 'https://developer.apple.com/tutorials/data/documentation/identitylookup.json',
    'imagecapturecore': 'https://developer.apple.com/tutorials/data/documentation/imagecapturecore.json',
    'imageio': 'https://developer.apple.com/tutorials/data/documentation/imageio.json',
    'imageplayground': 'https://developer.apple.com/tutorials/data/documentation/imageplayground.json',
    'immersivemediasupport': 'https://developer.apple.com/tutorials/data/documentation/immersivemediasupport.json',
    'inputmethodkit': 'https://developer.apple.com/tutorials/data/documentation/inputmethodkit.json',
    'installer_js': 'https://developer.apple.com/tutorials/data/documentation/installer_js.json',
    'iobluetooth': 'https://developer.apple.com/tutorials/data/documentation/iobluetooth.json',
    'iobluetoothui': 'https://developer.apple.com/tutorials/data/documentation/iobluetoothui.json',
    'iokit': 'https://developer.apple.com/tutorials/data/documentation/iokit.json',
    'iosurface': 'https://developer.apple.com/tutorials/data/documentation/iosurface.json',
    'iousbhost': 'https://developer.apple.com/tutorials/data/documentation/iousbhost.json',
    'ituneslibrary': 'https://developer.apple.com/tutorials/data/documentation/ituneslibrary.json',
    'iworkdocumentexportingapi': 'https://developer.apple.com/tutorials/data/documentation/iworkdocumentexportingapi.json',
    'javascriptcore': 'https://developer.apple.com/tutorials/data/documentation/javascriptcore.json',
    'journalingsuggestions': 'https://developer.apple.com/tutorials/data/documentation/journalingsuggestions.json',
    'latentsemanticmapping': 'https://developer.apple.com/tutorials/data/documentation/latentsemanticmapping.json',
    'lightweightcoderequirements': 'https://developer.apple.com/tutorials/data/documentation/lightweightcoderequirements.json',
    'linkpresentation': 'https://developer.apple.com/tutorials/data/documentation/linkpresentation.json',
    'livecommunicationkit': 'https://developer.apple.com/tutorials/data/documentation/livecommunicationkit.json',
    'livephotoskitjs': 'https://developer.apple.com/tutorials/data/documentation/livephotoskitjs.json',
    'localauthentication': 'https://developer.apple.com/tutorials/data/documentation/localauthentication.json',
    'localauthenticationembeddedui': 'https://developer.apple.com/tutorials/data/documentation/localauthenticationembeddedui.json',
    'lockedcameracapture': 'https://developer.apple.com/tutorials/data/documentation/lockedcameracapture.json',
    'mailkit': 'https://developer.apple.com/tutorials/data/documentation/mailkit.json',
    'managedapp': 'https://developer.apple.com/tutorials/data/documentation/managedapp.json',
    'managedappdistribution': 'https://developer.apple.com/tutorials/data/documentation/managedappdistribution.json',
    'managedsettings': 'https://developer.apple.com/tutorials/data/documentation/managedsettings.json',
    'managedsettingsui': 'https://developer.apple.com/tutorials/data/documentation/managedsettingsui.json',
    'mapkitjs': 'https://developer.apple.com/tutorials/data/documentation/mapkitjs.json',
    'marketplacekit': 'https://developer.apple.com/tutorials/data/documentation/marketplacekit.json',
    'matter': 'https://developer.apple.com/tutorials/data/documentation/matter.json',
    'mattersupport': 'https://developer.apple.com/tutorials/data/documentation/mattersupport.json',
    'mediaaccessibility': 'https://developer.apple.com/tutorials/data/documentation/mediaaccessibility.json',
    'mediadevice': 'https://developer.apple.com/tutorials/data/documentation/mediadevice.json',
    'mediaextension': 'https://developer.apple.com/tutorials/data/documentation/mediaextension.json',
    'mediaintelligence': 'https://developer.apple.com/tutorials/data/documentation/mediaintelligence.json',
    'mediaintents': 'https://developer.apple.com/tutorials/data/documentation/mediaintents.json',
    'medialibrary': 'https://developer.apple.com/tutorials/data/documentation/medialibrary.json',
    'mediaplayer': 'https://developer.apple.com/tutorials/data/documentation/mediaplayer.json',
    'mediasetup': 'https://developer.apple.com/tutorials/data/documentation/mediasetup.json',
    'mediatoolbox': 'https://developer.apple.com/tutorials/data/documentation/mediatoolbox.json',
    'merchanttokennotificationservices': 'https://developer.apple.com/tutorials/data/documentation/merchanttokennotificationservices.json',
    'messages': 'https://developer.apple.com/tutorials/data/documentation/messages.json',
    'metal': 'https://developer.apple.com/tutorials/data/documentation/metal.json',
    'metalfx': 'https://developer.apple.com/tutorials/data/documentation/metalfx.json',
    'metalkit': 'https://developer.apple.com/tutorials/data/documentation/metalkit.json',
    'metalperformanceshaders': 'https://developer.apple.com/tutorials/data/documentation/metalperformanceshaders.json',
    'metalperformanceshadersgraph': 'https://developer.apple.com/tutorials/data/documentation/metalperformanceshadersgraph.json',
    'metrickit': 'https://developer.apple.com/tutorials/data/documentation/metrickit.json',
    'mididriverkit': 'https://developer.apple.com/tutorials/data/documentation/mididriverkit.json',
    'mlcompute': 'https://developer.apple.com/tutorials/data/documentation/mlcompute.json',
    'modelio': 'https://developer.apple.com/tutorials/data/documentation/modelio.json',
    'musickit': 'https://developer.apple.com/tutorials/data/documentation/musickit.json',
    'musicunderstanding': 'https://developer.apple.com/tutorials/data/documentation/musicunderstanding.json',
    'nearbyinteraction': 'https://developer.apple.com/tutorials/data/documentation/nearbyinteraction.json',
    'network': 'https://developer.apple.com/tutorials/data/documentation/network.json',
    'networkextension': 'https://developer.apple.com/tutorials/data/documentation/networkextension.json',
    'networkingdriverkit': 'https://developer.apple.com/tutorials/data/documentation/networkingdriverkit.json',
    'notaryapi': 'https://developer.apple.com/tutorials/data/documentation/notaryapi.json',
    'notificationcenter': 'https://developer.apple.com/tutorials/data/documentation/notificationcenter.json',
    'nowplaying': 'https://developer.apple.com/tutorials/data/documentation/nowplaying.json',
    'objectivec': 'https://developer.apple.com/tutorials/data/documentation/objectivec.json',
    'observation': 'https://developer.apple.com/tutorials/data/documentation/observation.json',
    'opendirectory': 'https://developer.apple.com/tutorials/data/documentation/opendirectory.json',
    'opengles': 'https://developer.apple.com/tutorials/data/documentation/opengles.json',
    'os': 'https://developer.apple.com/tutorials/data/documentation/os.json',
    'oslog': 'https://developer.apple.com/tutorials/data/documentation/oslog.json',
    'packagedescription': 'https://developer.apple.com/tutorials/data/documentation/packagedescription.json',
    'paperkit': 'https://developer.apple.com/tutorials/data/documentation/paperkit.json',
    'paravirtualizedgraphics': 'https://developer.apple.com/tutorials/data/documentation/paravirtualizedgraphics.json',
    'passkit': 'https://developer.apple.com/tutorials/data/documentation/passkit.json',
    'pcidriverkit': 'https://developer.apple.com/tutorials/data/documentation/pcidriverkit.json',
    'pencilkit': 'https://developer.apple.com/tutorials/data/documentation/pencilkit.json',
    'permissionkit': 'https://developer.apple.com/tutorials/data/documentation/permissionkit.json',
    'phase': 'https://developer.apple.com/tutorials/data/documentation/phase.json',
    'photokit': 'https://developer.apple.com/tutorials/data/documentation/photokit.json',
    'playgroundbluetooth': 'https://developer.apple.com/tutorials/data/documentation/playgroundbluetooth.json',
    'playgroundsupport': 'https://developer.apple.com/tutorials/data/documentation/playgroundsupport.json',
    'preferencepanes': 'https://developer.apple.com/tutorials/data/documentation/preferencepanes.json',
    'professional_video_applications': 'https://developer.apple.com/tutorials/data/documentation/professional_video_applications.json',
    'proximityreader': 'https://developer.apple.com/tutorials/data/documentation/proximityreader.json',
    'pushkit': 'https://developer.apple.com/tutorials/data/documentation/pushkit.json',
    'pushtotalk': 'https://developer.apple.com/tutorials/data/documentation/pushtotalk.json',
    'quartz': 'https://developer.apple.com/tutorials/data/documentation/quartz.json',
    'quicklook': 'https://developer.apple.com/tutorials/data/documentation/quicklook.json',
    'quicklookthumbnailing': 'https://developer.apple.com/tutorials/data/documentation/quicklookthumbnailing.json',
    'quicklookui': 'https://developer.apple.com/tutorials/data/documentation/quicklookui.json',
    'quicktime-file-format': 'https://developer.apple.com/tutorials/data/documentation/quicktime-file-format.json',
    'realitycomposerpro': 'https://developer.apple.com/tutorials/data/documentation/realitycomposerpro.json',
    'regexbuilder': 'https://developer.apple.com/tutorials/data/documentation/regexbuilder.json',
    'relevancekit': 'https://developer.apple.com/tutorials/data/documentation/relevancekit.json',
    'replaykit': 'https://developer.apple.com/tutorials/data/documentation/replaykit.json',
    'retentionmessaging': 'https://developer.apple.com/tutorials/data/documentation/retentionmessaging.json',
    'roomplan': 'https://developer.apple.com/tutorials/data/documentation/roomplan.json',
    'rosterapi': 'https://developer.apple.com/tutorials/data/documentation/rosterapi.json',
    'safari-developer-tools': 'https://developer.apple.com/tutorials/data/documentation/safari-developer-tools.json',
    'safariservices': 'https://developer.apple.com/tutorials/data/documentation/safariservices.json',
    'safetykit': 'https://developer.apple.com/tutorials/data/documentation/safetykit.json',
    'samplecode': 'https://developer.apple.com/tutorials/data/documentation/samplecode.json',
    'scenekit': 'https://developer.apple.com/tutorials/data/documentation/scenekit.json',
    'screencapturekit': 'https://developer.apple.com/tutorials/data/documentation/screencapturekit.json',
    'screensaver': 'https://developer.apple.com/tutorials/data/documentation/screensaver.json',
    'screentime': 'https://developer.apple.com/tutorials/data/documentation/screentime.json',
    'scriptingbridge': 'https://developer.apple.com/tutorials/data/documentation/scriptingbridge.json',
    'scsicontrollerdriverkit': 'https://developer.apple.com/tutorials/data/documentation/scsicontrollerdriverkit.json',
    'scsiperipheralsdriverkit': 'https://developer.apple.com/tutorials/data/documentation/scsiperipheralsdriverkit.json',
    'secureelementcredential': 'https://developer.apple.com/tutorials/data/documentation/secureelementcredential.json',
    'securityfoundation': 'https://developer.apple.com/tutorials/data/documentation/securityfoundation.json',
    'securityinterface': 'https://developer.apple.com/tutorials/data/documentation/securityinterface.json',
    'sensitivecontentanalysis': 'https://developer.apple.com/tutorials/data/documentation/sensitivecontentanalysis.json',
    'sensorkit': 'https://developer.apple.com/tutorials/data/documentation/sensorkit.json',
    'serialdriverkit': 'https://developer.apple.com/tutorials/data/documentation/serialdriverkit.json',
    'servicemanagement': 'https://developer.apple.com/tutorials/data/documentation/servicemanagement.json',
    'servicesaccountlinking': 'https://developer.apple.com/tutorials/data/documentation/servicesaccountlinking.json',
    'shadergraph': 'https://developer.apple.com/tutorials/data/documentation/shadergraph.json',
    'sharedwithyou': 'https://developer.apple.com/tutorials/data/documentation/sharedwithyou.json',
    'shazamkit': 'https://developer.apple.com/tutorials/data/documentation/shazamkit.json',
    'signinwithapple': 'https://developer.apple.com/tutorials/data/documentation/signinwithapple.json',
    'sirievaluations': 'https://developer.apple.com/tutorials/data/documentation/sirievaluations.json',
    'sirieventsuggestionsmarkup': 'https://developer.apple.com/tutorials/data/documentation/sirieventsuggestionsmarkup.json',
    'sirikit': 'https://developer.apple.com/tutorials/data/documentation/sirikit.json',
    'sirikitcloudmedia': 'https://developer.apple.com/tutorials/data/documentation/sirikitcloudmedia.json',
    'skadnetworkforwebads': 'https://developer.apple.com/tutorials/data/documentation/skadnetworkforwebads.json',
    'snapshots': 'https://developer.apple.com/tutorials/data/documentation/snapshots.json',
    'social': 'https://developer.apple.com/tutorials/data/documentation/social.json',
    'soundanalysis': 'https://developer.apple.com/tutorials/data/documentation/soundanalysis.json',
    'spatial': 'https://developer.apple.com/tutorials/data/documentation/spatial.json',
    'spatialpreview': 'https://developer.apple.com/tutorials/data/documentation/spatialpreview.json',
    'statereporting': 'https://developer.apple.com/tutorials/data/documentation/statereporting.json',
    'storekit': 'https://developer.apple.com/tutorials/data/documentation/storekit.json',
    'storekittest': 'https://developer.apple.com/tutorials/data/documentation/storekittest.json',
    'suggestedactions': 'https://developer.apple.com/tutorials/data/documentation/suggestedactions.json',
    'swift-playgrounds': 'https://developer.apple.com/tutorials/data/documentation/swift-playgrounds.json',
    'symbols': 'https://developer.apple.com/tutorials/data/documentation/symbols.json',
    'synchronization': 'https://developer.apple.com/tutorials/data/documentation/synchronization.json',
    'system': 'https://developer.apple.com/tutorials/data/documentation/system.json',
    'systemconfiguration': 'https://developer.apple.com/tutorials/data/documentation/systemconfiguration.json',
    'systemextensions': 'https://developer.apple.com/tutorials/data/documentation/systemextensions.json',
    'tabletopkit': 'https://developer.apple.com/tutorials/data/documentation/tabletopkit.json',
    'tabulardata': 'https://developer.apple.com/tutorials/data/documentation/tabulardata.json',
    'technologies': 'https://developer.apple.com/tutorials/data/documentation/technologies.json',
    'technologyoverviews': 'https://developer.apple.com/tutorials/data/documentation/technologyoverviews.json',
    'technotes': 'https://developer.apple.com/tutorials/data/documentation/technotes.json',
    'telephonymessagingkit': 'https://developer.apple.com/tutorials/data/documentation/telephonymessagingkit.json',
    'testing': 'https://developer.apple.com/tutorials/data/documentation/testing.json',
    'threadnetwork': 'https://developer.apple.com/tutorials/data/documentation/threadnetwork.json',
    'tipkit': 'https://developer.apple.com/tutorials/data/documentation/tipkit.json',
    'touchcontroller': 'https://developer.apple.com/tutorials/data/documentation/touchcontroller.json',
    'translation': 'https://developer.apple.com/tutorials/data/documentation/translation.json',
    'translationuiprovider': 'https://developer.apple.com/tutorials/data/documentation/translationuiprovider.json',
    'trustinsights': 'https://developer.apple.com/tutorials/data/documentation/trustinsights.json',
    'tvml': 'https://developer.apple.com/tutorials/data/documentation/tvml.json',
    'tvmljs': 'https://developer.apple.com/tutorials/data/documentation/tvmljs.json',
    'tvmlkit': 'https://developer.apple.com/tutorials/data/documentation/tvmlkit.json',
    'tvservices': 'https://developer.apple.com/tutorials/data/documentation/tvservices.json',
    'tvuikit': 'https://developer.apple.com/tutorials/data/documentation/tvuikit.json',
    'usbdriverkit': 'https://developer.apple.com/tutorials/data/documentation/usbdriverkit.json',
    'usbserialdriverkit': 'https://developer.apple.com/tutorials/data/documentation/usbserialdriverkit.json',
    'usd': 'https://developer.apple.com/tutorials/data/documentation/usd.json',
    'usdkit': 'https://developer.apple.com/tutorials/data/documentation/usdkit.json',
    'usernotificationsui': 'https://developer.apple.com/tutorials/data/documentation/usernotificationsui.json',
    'videodriverkit': 'https://developer.apple.com/tutorials/data/documentation/videodriverkit.json',
    'videosubscriberaccount': 'https://developer.apple.com/tutorials/data/documentation/videosubscriberaccount.json',
    'videotoolbox': 'https://developer.apple.com/tutorials/data/documentation/videotoolbox.json',
    'virtualization': 'https://developer.apple.com/tutorials/data/documentation/virtualization.json',
    'vision': 'https://developer.apple.com/tutorials/data/documentation/vision.json',
    'visionentitlementservices': 'https://developer.apple.com/tutorials/data/documentation/visionentitlementservices.json',
    'visionos': 'https://developer.apple.com/tutorials/data/documentation/visionos.json',
    'visualintelligence': 'https://developer.apple.com/tutorials/data/documentation/visualintelligence.json',
    'vmnet': 'https://developer.apple.com/tutorials/data/documentation/vmnet.json',
    'walletorders': 'https://developer.apple.com/tutorials/data/documentation/walletorders.json',
    'walletpasses': 'https://developer.apple.com/tutorials/data/documentation/walletpasses.json',
    'watchconnectivity': 'https://developer.apple.com/tutorials/data/documentation/watchconnectivity.json',
    'watchkit': 'https://developer.apple.com/tutorials/data/documentation/watchkit.json',
    'watchos-apps': 'https://developer.apple.com/tutorials/data/documentation/watchos-apps.json',
    'weatherkit': 'https://developer.apple.com/tutorials/data/documentation/weatherkit.json',
    'weatherkitrestapi': 'https://developer.apple.com/tutorials/data/documentation/weatherkitrestapi.json',
    'webkitjs': 'https://developer.apple.com/tutorials/data/documentation/webkitjs.json',
    'widgetkit': 'https://developer.apple.com/tutorials/data/documentation/widgetkit.json',
    'wifiaware': 'https://developer.apple.com/tutorials/data/documentation/wifiaware.json',
    'wifiinfrastructure': 'https://developer.apple.com/tutorials/data/documentation/wifiinfrastructure.json',
    'wirelessinsights': 'https://developer.apple.com/tutorials/data/documentation/wirelessinsights.json',
    'workoutkit': 'https://developer.apple.com/tutorials/data/documentation/workoutkit.json',
    'xcodekit': 'https://developer.apple.com/tutorials/data/documentation/xcodekit.json',
    'xcselect': 'https://developer.apple.com/tutorials/data/documentation/xcselect.json',
    'xctest': 'https://developer.apple.com/tutorials/data/documentation/xctest.json',
    'xcuiautomation': 'https://developer.apple.com/tutorials/data/documentation/xcuiautomation.json',
    'xpc': 'https://developer.apple.com/tutorials/data/documentation/xpc.json',
    # What's New (per-technology updates + WWDC summaries)
    'updates': 'https://developer.apple.com/tutorials/data/documentation/updates.json',
    # Release notes
    'ios-ipados-release-notes': 'https://developer.apple.com/tutorials/data/documentation/ios-ipados-release-notes.json',
    'macos-release-notes': 'https://developer.apple.com/tutorials/data/documentation/macos-release-notes.json',
    'tvos-release-notes': 'https://developer.apple.com/tutorials/data/documentation/tvos-release-notes.json',
    'watchos-release-notes': 'https://developer.apple.com/tutorials/data/documentation/watchos-release-notes.json',
    'visionos-release-notes': 'https://developer.apple.com/tutorials/data/documentation/visionos-release-notes.json',
    'xcode-release-notes': 'https://developer.apple.com/tutorials/data/documentation/xcode-release-notes.json',
    'safari-release-notes': 'https://developer.apple.com/tutorials/data/documentation/safari-release-notes.json',
}


class DocumentationCrawler:
    """Crawls Apple's documentation API to discover all pages"""

    def __init__(self, output_dir: Path, frameworks: List[str] = None, base_dir: Path = None,
                 reuse_from: Path = None):
        self.output_dir = Path(output_dir)
        self.frameworks = frameworks or list(FRAMEWORK_ROOTS.keys())

        # Prior archive to reuse: pages are requested with If-None-Match and, on a 304,
        # copied from the old raw JSON instead of being downloaded again
        self.reuse: Dict[str, dict] = {}
        self.stats = {'fetched': 0, 'reused': 0}

        # Pages are saved as they are crawled, so 02_download_json.py has nothing left to fetch
        self.base_dir = Path(base_dir) if base_dir else self.output_dir
        self.raw_json_dir = self.base_dir / 'raw-json'
        self.manifest_file = self.base_dir / '.docsync' / 'manifest.json'
        self.manifest: Dict[str, dict] = {}
        if self.manifest_file.exists():
            with open(self.manifest_file) as f:
                self.manifest = json.load(f)
        self.last_save = time.time()
        if reuse_from:
            self.load_reuse(Path(reuse_from))

        # State tracking
        self.discovered_urls: Set[str] = set()
        self.processed_urls: Set[str] = set()
        self.url_metadata: Dict[str, dict] = {}

        # Progress tracking
        self.pbar = None

        # Rate limiting
        self.rate_limiter = AdaptiveRateLimiter(rate=10.0, max_rate=15.0)

        # User agent
        self.user_agent = 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36'

    def load_reuse(self, reuse_dir: Path):
        """Index a prior archive's manifest by lowercase doc path (newest entry wins)"""
        manifest_file = reuse_dir / '.docsync' / 'manifest.json'
        if not manifest_file.exists():
            print(f"Warning: no manifest to reuse at {manifest_file}")
            return
        with open(manifest_file) as f:
            old = json.load(f)
        wanted = set(self.frameworks)
        for key, entry in old.items():
            low = key.lower()
            if low.split('/')[0] not in wanted or not entry.get('etag'):
                continue
            stamp = entry.get('updated_at') or entry.get('downloaded_at') or ''
            prev = self.reuse.get(low)
            if prev and prev['_stamp'] >= stamp:
                continue
            self.reuse[low] = {**entry, '_src': str(reuse_dir / entry['local_path']), '_stamp': stamp}
        print(f"Reusing up to {len(self.reuse)} pages from {reuse_dir}")

    def save_manifest(self):
        """Atomically save the manifest (a kill mid-save can't truncate it)"""
        self.manifest_file.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.manifest_file.with_suffix('.json.tmp')
        with open(tmp, 'w') as f:
            json.dump(self.manifest, f, indent=2)
        tmp.replace(self.manifest_file)

    def save_state(self):
        """Save current discovery state"""
        state_file = self.output_dir / 'discovery_state.json'
        state = {
            'discovered_urls': list(self.discovered_urls),
            'processed_urls': list(self.processed_urls),
            'url_metadata': self.url_metadata,
        }
        tmp = state_file.with_suffix('.json.tmp')
        with open(tmp, 'w') as f:
            json.dump(state, f, indent=2)
        tmp.replace(state_file)  # atomic, so an interrupted crawl can always --resume

    def load_state(self):
        """Load previous discovery state for resume"""
        state_file = self.output_dir / 'discovery_state.json'
        if state_file.exists():
            with open(state_file, 'r') as f:
                state = json.load(f)
            self.discovered_urls = set(state['discovered_urls'])
            self.processed_urls = set(state['processed_urls'])
            self.url_metadata = state['url_metadata']
            print(f"Resumed: {len(self.processed_urls)} processed, {len(self.discovered_urls) - len(self.processed_urls)} remaining")

    def normalize_identifier(self, identifier: str) -> str:
        """Normalize Apple doc identifier to path format

        Converts:
        - doc://com.apple.foundation/documentation/Foundation/NSString -> foundation/NSString
        - /documentation/swift/string -> swift/string
        - swift/string -> swift/string
        """
        # Remove doc:// prefix if present
        if identifier.startswith('doc://'):
            # Format: doc://com.apple.FRAMEWORK/documentation/PATH
            identifier = identifier.replace('doc://', '')
            # Remove com.apple. prefix
            if identifier.startswith('com.apple.'):
                identifier = identifier.replace('com.apple.', '', 1)
            # Extract path after /documentation/
            if '/documentation/' in identifier:
                parts = identifier.split('/documentation/', 1)
                if len(parts) == 2:
                    path = parts[1]
                    # Path often starts with framework name in CamelCase
                    # e.g., "Foundation/NSString" or "VideoToolbox/VTSession..."
                    # Just use the path directly (lowercase first segment is the framework)
                    if '/' in path:
                        # Convert first segment to lowercase to get framework
                        path_parts = path.split('/', 1)
                        framework = path_parts[0].lower()
                        rest = path_parts[1]
                        identifier = f"{framework}/{rest}"
                    else:
                        # Single segment, just lowercase it
                        identifier = path.lower()

        # Remove leading /documentation/ if present
        if identifier.startswith('/documentation/'):
            identifier = identifier.replace('/documentation/', '', 1)

        # Apple doc URLs are case-insensitive and canonically lowercase; without this,
        # doc:// refs (CamelCase) and url refs (lowercase) produce duplicate pages.
        return identifier.lower()

    def extract_references_from_json(self, data: dict, base_path: str) -> Set[str]:
        """Extract all documentation references from JSON data"""
        references = set()

        # Extract from topicSections
        if 'topicSections' in data:
            for section in data['topicSections']:
                if 'identifiers' in section:
                    for identifier in section['identifiers']:
                        normalized = self.normalize_identifier(identifier)
                        if normalized:
                            references.add(normalized)

        # Extract from references section
        if 'references' in data:
            for ref_id, ref_data in data['references'].items():
                if isinstance(ref_data, dict) and ref_data.get('type') in ['topic', 'method', 'property', 'class', 'structure', 'protocol', 'enum']:
                    if 'url' in ref_data:
                        # Convert relative URL to full path
                        url = ref_data['url']
                        normalized = self.normalize_identifier(url)
                        if normalized:
                            references.add(normalized)

        # Extract from relationships
        if 'relationshipsSections' in data:
            for section in data.get('relationshipsSections', []):
                if 'identifiers' in section:
                    for identifier in section['identifiers']:
                        normalized = self.normalize_identifier(identifier)
                        if normalized:
                            references.add(normalized)

        return references

    async def fetch_json(self, session: aiohttp.ClientSession, url: str, etag: str = None):
        """Fetch a page. Returns (status, data, raw_text, headers) for 200/304, else None"""
        result = await fetch(session, self.rate_limiter, url, self.user_agent, etag=etag)
        if result is None:
            return None
        status, text, headers = result
        if status == 200:
            return status, json.loads(text), text, headers
        if status == 304:
            return status, None, None, headers
        if status == 404:
            print(f"\nWarning: 404 Not Found: {url}")
        else:
            print(f"\nError {status} fetching {url}")
        return None

    async def crawl_page(self, session: aiohttp.ClientSession, doc_path: str) -> Set[str]:
        """Crawl a single documentation page and extract references"""
        if doc_path in self.processed_urls:
            return set()

        url = f'https://developer.apple.com/tutorials/data/documentation/{doc_path}.json'

        # Conditional request when we already hold a copy of this page
        prior = self.reuse.get(doc_path)
        if prior and not Path(prior['_src']).exists():
            prior = None
        result = await self.fetch_json(session, url, etag=prior['etag'] if prior else None)
        if not result:
            self.processed_urls.add(doc_path)
            return set()
        status, data, text, resp_headers = result

        output_file = self.raw_json_dir / f'{doc_path}.json'
        output_file.parent.mkdir(parents=True, exist_ok=True)
        if status == 304:
            # Unchanged: copy the prior JSON and keep its manifest entry
            with open(prior['_src']) as f:
                data = json.load(f)
            with open(output_file, 'w') as f:
                json.dump(data, f, indent=2)
            entry = {k: v for k, v in prior.items() if not k.startswith('_')}
            entry.update({
                'url': url,
                'local_path': str(output_file.relative_to(self.base_dir)),
                'verified_at': datetime.now().isoformat(),
            })
            self.manifest[doc_path] = entry
            self.stats['reused'] += 1
        else:
            # Save the page now instead of fetching it again in a separate download step
            with open(output_file, 'w') as f:
                json.dump(data, f, indent=2)
            self.manifest[doc_path] = {
                'url': url,
                'local_path': str(output_file.relative_to(self.base_dir)),
                'last_modified': resp_headers.get('Last-Modified', ''),
                'etag': resp_headers.get('ETag', ''),
                'sha256': hashlib.sha256(text.encode('utf-8')).hexdigest(),
                'downloaded_at': datetime.now().isoformat(),
                'title': data.get('metadata', {}).get('title', ''),
                'role': data.get('metadata', {}).get('role', ''),
            }
            self.stats['fetched'] += 1

        # Store metadata
        self.url_metadata[doc_path] = {
            'url': url,
            'title': data.get('metadata', {}).get('title', ''),
            'role': data.get('metadata', {}).get('role', ''),
        }

        # Extract new references
        references = self.extract_references_from_json(data, doc_path)

        # Mark as processed
        self.processed_urls.add(doc_path)

        # Update progress
        if self.pbar:
            self.pbar.update(1)

        return references

    async def crawl_framework(self, framework: str):
        """Crawl all documentation for a single framework"""
        print(f"\nCrawling {framework}...")

        # Initialize with framework root
        root_path = framework
        self.discovered_urls.add(root_path)

        async with aiohttp.ClientSession() as session:
            while True:
                # Find unprocessed URLs
                to_process = self.discovered_urls - self.processed_urls

                if not to_process:
                    break

                # Create progress bar
                if self.pbar:
                    self.pbar.close()
                self.pbar = tqdm(total=len(to_process), desc=f"{framework} pages")

                # Process pages in batches
                batch_size = 10
                to_process_list = list(to_process)

                for i in range(0, len(to_process_list), batch_size):
                    batch = to_process_list[i:i+batch_size]

                    # Fetch batch concurrently
                    tasks = [self.crawl_page(session, doc_path) for doc_path in batch]
                    results = await asyncio.gather(*tasks)

                    # Add new discovered URLs (only from current framework)
                    for new_refs in results:
                        for ref in new_refs:
                            # Only add URLs that belong to current framework
                            if ref.startswith(f"{framework}/") or ref == framework:
                                if ref not in self.discovered_urls:
                                    self.discovered_urls.add(ref)

                    # Save state and manifest periodically (time-based: both grow large)
                    if time.time() - self.last_save > 60:
                        self.save_state()
                        self.save_manifest()
                        self.last_save = time.time()

                if self.pbar:
                    self.pbar.close()

        # Final save
        self.save_state()
        self.save_manifest()

    async def crawl_all(self):
        """Crawl all specified frameworks"""
        for framework in self.frameworks:
            if framework not in FRAMEWORK_ROOTS:
                print(f"Warning: Unknown framework '{framework}', skipping")
                continue

            await self.crawl_framework(framework)

        # Save final index
        self.save_index()

    def save_index(self):
        """Save complete discovered index"""
        index_file = self.output_dir / 'index.json'

        # Merge into the existing index so per-framework runs don't drop other frameworks
        index = {'frameworks': {}, 'metadata': {}}
        if index_file.exists():
            with open(index_file) as f:
                index = json.load(f)

        crawled = {}
        for url in self.discovered_urls:
            crawled.setdefault(url.split('/')[0], []).append(url)
        index['frameworks'].update({fw: sorted(urls) for fw, urls in crawled.items()})
        index.setdefault('metadata', {}).update(self.url_metadata)
        index['total_pages'] = sum(len(v) for v in index['frameworks'].values())
        index['processed_pages'] = len(index['metadata'])

        with open(index_file, 'w') as f:
            json.dump(index, f, indent=2)

        print(f"\nIndex saved to {index_file}")
        print(f"Total pages discovered: {len(self.discovered_urls)}")
        print(f"Total processed: {len(self.processed_urls)}")
        print(f"Downloaded: {self.stats['fetched']}, reused unchanged (304): {self.stats['reused']}, "
              f"final rate: {self.rate_limiter.rate:.1f} req/s")

        # Print framework breakdown
        print("\nFramework breakdown:")
        for framework, urls in sorted(crawled.items()):
            print(f"  {framework}: {len(urls)} pages")


async def main():
    parser = argparse.ArgumentParser(description='Discover Apple documentation pages')
    parser.add_argument('--frameworks', nargs='+', help='Frameworks to crawl (default: all)')
    parser.add_argument('--output', default='.', help='Directory for index.json and discovery state (default: project directory)')
    parser.add_argument('--base-dir', default=None, help='Archive directory for raw-json/ and .docsync/ (default: --output)')
    parser.add_argument('--reuse-from', default=None,
                        help='Prior archive directory; unchanged pages (HTTP 304) are copied from it instead of downloaded')
    parser.add_argument('--resume', action='store_true', help='Resume from previous state')

    args = parser.parse_args()

    output_dir = Path(__file__).parent.parent / args.output if args.output == '.' else Path(args.output)
    output_dir.mkdir(parents=True, exist_ok=True)

    crawler = DocumentationCrawler(
        output_dir=output_dir,
        frameworks=args.frameworks,
        base_dir=Path(args.base_dir) if args.base_dir else output_dir,
        reuse_from=Path(args.reuse_from) if args.reuse_from else None,
    )

    if args.resume:
        crawler.load_state()

    await crawler.crawl_all()

    print("\nDiscovery complete!")


if __name__ == '__main__':
    asyncio.run(main())
