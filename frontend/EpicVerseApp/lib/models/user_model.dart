class UserModel {
  final String id;
  final String displayName;
  final String email;
  final String primaryLanguage;
  final List<String> preferredLanguages;
  final String activeArc;
  final int level;

  final String? profilePicture;

  UserModel({
    required this.id,
    required this.displayName,
    required this.email,
    required this.primaryLanguage,
    required this.preferredLanguages,
    this.activeArc = 'Origin-Arc',
    this.level = 1,
    this.profilePicture,
  });

  factory UserModel.fromJson(Map<String, dynamic> json) {
    return UserModel(
      id: json['uid'] ?? json['id'] ?? '',
      displayName: json['display_name'] ?? json['displayName'] ?? json['name'] ?? 'User',
      email: json['email'] ?? '',
      primaryLanguage: json['primary_language'] ?? json['primaryLanguage'] ?? 'English',
      preferredLanguages: json['preferred_languages'] != null 
          ? List<String>.from(json['preferred_languages'])
          : (json['preferredLanguages'] != null 
              ? List<String>.from(json['preferredLanguages'])
              : ['English']),
      activeArc: json['active_arc'] ?? json['activeArc'] ?? 'Origin-Arc',
      level: json['level'] ?? 1,
      profilePicture: json['profile_picture'] ?? json['profilePicture'],
    );
  }

  Map<String, dynamic> toJson() {
    return {
      'uid': id,
      'display_name': displayName,
      'email': email,
      'primary_language': primaryLanguage,
      'preferred_languages': preferredLanguages,
      'active_arc': activeArc,
      'level': level,
      'profile_picture': profilePicture,
    };
  }

  UserModel copyWith({
    String? displayName,
    String? email,
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
      primaryLanguage: primaryLanguage ?? this.primaryLanguage,
      preferredLanguages: preferredLanguages ?? this.preferredLanguages,
      activeArc: activeArc ?? this.activeArc,
      level: level ?? this.level,
      profilePicture: profilePicture ?? this.profilePicture,
    );
  }
}
