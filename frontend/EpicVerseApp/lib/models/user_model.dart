class UserModel {
  final String id;
  final String displayName;
  final String? email;
  final String? phoneNumber;
  final String primaryLanguage;
  final List<String> preferredLanguages;
  final String activeArc;
  final int level;

  final String? profilePicture;
  final String? inviteCode;
  final String? sessionId;

  UserModel({
    required this.id,
    required this.displayName,
    this.email,
    this.phoneNumber,
    this.primaryLanguage = 'English',
    this.preferredLanguages = const ['English'],
    this.activeArc = 'Origin-Arc',
    this.level = 1,
    this.profilePicture,
    this.inviteCode,
    this.sessionId,
  });

  factory UserModel.fromJson(Map<String, dynamic> json) {
    return UserModel(
      id: json['uid'] ?? json['id'] ?? '',
      displayName: json['display_name'] ?? json['displayName'] ?? json['name'] ?? 'User',
      email: json['email'],
      phoneNumber: json['phone_number'] ?? json['phoneNumber'],
      primaryLanguage: json['primary_language'] ?? json['primaryLanguage'] ?? 'English',
      preferredLanguages: json['preferred_languages'] != null 
          ? List<String>.from(json['preferred_languages'])
          : (json['preferredLanguages'] != null 
              ? List<String>.from(json['preferredLanguages'])
              : ['English']),
      activeArc: json['active_arc'] ?? json['activeArc'] ?? 'Origin-Arc',
      level: json['level'] ?? 1,
      profilePicture: json['profile_picture'] ?? json['profilePicture'],
      inviteCode: json['invite_code'] ?? json['inviteCode'],
      sessionId: json['session_id'] ?? json['sessionId'],
    );
  }

  Map<String, dynamic> toJson() {
    return {
      'uid': id,
      'display_name': displayName,
      'email': email,
      'phone_number': phoneNumber,
      'primary_language': primaryLanguage,
      'preferred_languages': preferredLanguages,
      'active_arc': activeArc,
      'level': level,
      'profile_picture': profilePicture,
      'invite_code': inviteCode,
      'session_id': sessionId,
    };
  }

  UserModel copyWith({
    String? displayName,
    String? email,
    String? phoneNumber,
    String? primaryLanguage,
    List<String>? preferredLanguages,
    String? activeArc,
    int? level,
    String? profilePicture,
  }) {
    return UserModel(
      id: id,
      displayName: displayName ?? this.displayName,
      email: email ?? this.email,
      phoneNumber: phoneNumber ?? this.phoneNumber,
      primaryLanguage: primaryLanguage ?? this.primaryLanguage,
      preferredLanguages: preferredLanguages ?? this.preferredLanguages,
      activeArc: activeArc ?? this.activeArc,
      level: level ?? this.level,
      profilePicture: profilePicture ?? this.profilePicture,
    );
  }
}
