import 'package:flutter_riverpod/flutter_riverpod.dart';
import '../models/user_model.dart';

class UserNotifier extends Notifier<UserModel?> {
  @override
  UserModel? build() {
    return null;
  }

  void setUser(UserModel user) {
    state = user;
  }

  void logout() {
    state = null;
  }

  void updateProfile({String? name, String? language}) {
    if (state != null) {
      state = state!.copyWith(
        displayName: name,
        primaryLanguage: language,
      );
    }
  }
}

final userProvider = NotifierProvider<UserNotifier, UserModel?>(UserNotifier.new);
