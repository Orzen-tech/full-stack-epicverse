import 'package:flutter/material.dart';
import 'package:dio/dio.dart';
import '../../core/constants/app_colors.dart';
import '../../core/network/api_config.dart';
import '../widgets/network_background.dart';

class FAQScreen extends StatefulWidget {
  const FAQScreen({super.key});

  @override
  State<FAQScreen> createState() => _FAQScreenState();
}

class _FAQScreenState extends State<FAQScreen> {
  final Dio _dio = Dio();
  List<Map<String, String>> _items = [];
  bool _isLoading = true;
  String? _error;

  @override
  void initState() {
    super.initState();
    _fetchFAQ();
  }

  Future<void> _fetchFAQ() async {
    try {
      final response = await _dio.get(
        '${ApiConfig.apiUrl}/faq',
        options: Options(headers: ApiConfig.headers),
      );
      if (response.statusCode == 200 && response.data['items'] is List) {
        final items = (response.data['items'] as List)
            .map((e) => {'question': e['question'] as String, 'answer': e['answer'] as String})
            .toList();
        setState(() {
          _items = items;
          _isLoading = false;
        });
      } else {
        setState(() {
          _error = 'Failed to load FAQ';
          _isLoading = false;
        });
      }
    } catch (e) {
      debugPrint('FAQ fetch error: $e');
      setState(() {
        _error = 'Could not load FAQ. Please check your connection.';
        _isLoading = false;
      });
    }
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      body: NetworkBackground(
        child: SafeArea(
          child: Column(
            children: [
              Padding(
                padding: const EdgeInsets.all(24.0),
                child: Row(
                  children: [
                    IconButton(
                      onPressed: () => Navigator.pop(context),
                      icon: const Icon(Icons.arrow_back, color: AppColors.textPrimary),
                    ),
                    const SizedBox(width: 16),
                    const Text(
                      'FAQ',
                      style: TextStyle(
                        color: AppColors.textPrimary,
                        fontSize: 24,
                        fontWeight: FontWeight.bold,
                      ),
                    ),
                  ],
                ),
              ),
              Expanded(
                child: _isLoading
                    ? const Center(child: CircularProgressIndicator(color: AppColors.primaryGold))
                    : _error != null
                        ? Center(
                            child: Padding(
                              padding: const EdgeInsets.all(24),
                              child: Text(_error!, style: const TextStyle(color: AppColors.textSecondary), textAlign: TextAlign.center),
                            ),
                          )
                        : ListView.separated(
                            padding: const EdgeInsets.symmetric(horizontal: 24, vertical: 8),
                            itemCount: _items.length,
                            separatorBuilder: (_, _) => const SizedBox(height: 12),
                            itemBuilder: (context, index) {
                              final item = _items[index];
                              return Container(
                                decoration: BoxDecoration(
                                  color: Colors.white.withValues(alpha: 0.05),
                                  borderRadius: BorderRadius.circular(12),
                                  border: Border.all(color: Colors.white.withValues(alpha: 0.1)),
                                ),
                                child: Theme(
                                  data: Theme.of(context).copyWith(dividerColor: Colors.transparent),
                                  child: ExpansionTile(
                                    tilePadding: const EdgeInsets.symmetric(horizontal: 16, vertical: 4),
                                    childrenPadding: const EdgeInsets.fromLTRB(16, 0, 16, 16),
                                    leading: const Icon(Icons.help_outline, color: AppColors.primaryGold, size: 20),
                                    title: Text(
                                      item['question']!,
                                      style: const TextStyle(
                                        color: AppColors.textPrimary,
                                        fontSize: 14,
                                        fontWeight: FontWeight.w600,
                                      ),
                                    ),
                                    iconColor: AppColors.primaryGold,
                                    collapsedIconColor: AppColors.textMuted,
                                    children: [
                                      Text(
                                        item['answer']!,
                                        style: const TextStyle(
                                          color: AppColors.textSecondary,
                                          fontSize: 14,
                                          height: 1.5,
                                        ),
                                      ),
                                    ],
                                  ),
                                ),
                              );
                            },
                          ),
              ),
            ],
          ),
        ),
      ),
    );
  }
}
